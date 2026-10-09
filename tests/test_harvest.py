import asyncio
import copy
import re
import time

import httpx
from pydantic import TypeAdapter

from mr_board.harvest import (
    ChannelHarvest,
    EmojiState,
    GitLabInfo,
    Harvester,
    MergeRequest,
    Snapshot,
)

from .test_parse import APP_POST

NOW = int(time.time() * 1000)


def mm_post(pid, message, root_id="", reactions=(), user="u_author"):
    return {
        "id": pid,
        "root_id": root_id,
        "create_at": NOW - 1000,
        "delete_at": 0,
        "user_id": user,
        "message": message,
        "metadata": {
            "reactions": [
                {"emoji_name": e, "user_id": u, "create_at": NOW - 900 + i}
                for i, (e, u) in enumerate(reactions)
            ]
        },
    }


POSTS = {
    "root": mm_post("root", APP_POST, reactions=[("eyes", "u_rev"), ("white_check_mark", "u_rev")]),
    "reply": mm_post("reply", "lgtm", root_id="root", user="u_rev"),
    "chat": mm_post("chat", "anyone up for coffee?"),
}


def make_transport(calls: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.url.host}{request.url.path}")
        path = request.url.path
        if request.url.host == "mm.test":
            assert request.headers["Authorization"] == "Bearer mm-token"
            if path.endswith("/channels/chan/posts"):
                page = int(request.url.params["page"])
                return httpx.Response(
                    200,
                    json={"order": list(POSTS) if page == 0 else [], "posts": POSTS},
                )
            if path.endswith("/users/ids"):
                return httpx.Response(
                    200,
                    json=[
                        {"id": "u_author", "username": "kekessle"},
                        {"id": "u_rev", "username": "rossilo"},
                    ],
                )
        if request.url.host == "gitlab.test":
            assert request.headers["PRIVATE-TOKEN"] == "gl-token"
            assert "epc%2Fccs%2Ftools%2Ffgc-web" in str(request.url)
            if path.endswith("/approvals"):
                return httpx.Response(
                    200, json={"approved_by": [{"user": {"username": "rossilo"}}]}
                )
            return httpx.Response(
                200,
                json={
                    "state": "opened",
                    "draft": False,
                    "author": {"username": "kekessle"},
                    "updated_at": "2026-10-05T10:00:00Z",
                    "has_conflicts": False,
                    "head_pipeline": {"status": "success"},
                },
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_harvest_end_to_end(settings):
    calls: list[str] = []
    data = asyncio.run(Harvester(settings, make_transport(calls)).harvest())

    assert len(data.mrs) == 1
    mr = data.mrs[0]
    assert mr.emoji_state is EmojiState.APPROVED
    assert mr.replies == 1
    assert mr.repliers == ["u_rev"]
    assert [r.emoji for r in mr.reactions] == ["eyes", "white_check_mark"]
    assert mr.gitlab.state == "opened"
    assert mr.gitlab.approved_by == ["rossilo"]
    assert data.users == {"u_author": "kekessle", "u_rev": "rossilo"}
    assert data.gitlab_checked is True


def test_snapshot_serialises_to_frontend_shape(settings):
    snapshot = asyncio.run(Harvester(settings, make_transport([])).harvest())
    body = TypeAdapter(Snapshot).dump_python(snapshot, mode="json")
    mr = body["mrs"][0]
    assert body["channel"] == "tools-reviews"
    assert (body["merged_days"], body["mm_url"]) == (7, "https://mm.test")
    assert mr["emoji_state"] == "approved"
    assert mr["reactions"][0] == {"emoji": "eyes", "user_id": "u_rev", "at": NOW - 900}
    assert mr["gitlab"]["approved_by"] == ["rossilo"]


def test_harvest_without_gitlab_token(settings):
    from dataclasses import replace

    calls: list[str] = []
    data = asyncio.run(
        Harvester(replace(settings, gitlab_token=""), make_transport(calls)).harvest()
    )
    assert data.gitlab_checked is False
    assert data.mrs[0].gitlab is None
    assert not any(c.startswith("gitlab.test") for c in calls)


def test_final_gitlab_states_are_cached(settings):
    calls: list[str] = []
    harvester = Harvester(settings, make_transport(calls))
    info = GitLabInfo(
        state="merged",
        draft=False,
        author="kekessle",
        updated_at="2026-10-01T10:00:00Z",
        merged_at="2026-10-01T10:00:00Z",
        merged_by="rossilo",
        approved_by=[],
        conflicts=False,
        pipeline="success",
    )
    harvester.gitlab._final_cache[
        "https://gitlab.cern.ch/epc/ccs/tools/fgc-web/-/merge_requests/1891"
    ] = info
    data = asyncio.run(harvester.harvest())
    assert data.mrs[0].gitlab is info
    assert not any(c.startswith("gitlab.test") for c in calls)


def test_channel_mrs_are_newest_first():
    from .test_parse import NO_APP_POST, post

    old, new = (MergeRequest.from_post(post(NO_APP_POST)) for _ in range(2))
    old.posted_at, new.posted_at = 1, 2
    channel = ChannelHarvest(mrs=[old, new], users={})
    assert channel.get_mrs() == [new, old]
    assert channel.mrs == [old, new]  # not sorted in place


class Channel:
    """A Mattermost channel whose posts can change between harvests."""

    def __init__(self, posts: dict[str, dict]):
        self.posts = posts
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v4")
        self.calls.append(path)
        if path == "/channels/chan/posts":
            listed = {k: p for k, p in self.posts.items() if p.get("listed", True)}
            page = int(request.url.params["page"])
            return httpx.Response(
                200, json={"order": list(listed) if page == 0 else [], "posts": listed}
            )
        if m := re.fullmatch(r"/posts/(\w+)/thread", path):
            pid = m[1]
            if pid not in self.posts:
                return httpx.Response(404, json={"message": "not found"})
            thread = {k: p for k, p in self.posts.items() if k == pid or p["root_id"] == pid}
            return httpx.Response(200, json={"order": list(thread), "posts": thread})
        if path == "/users/ids":
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    def harvester(self, settings) -> Harvester:
        from dataclasses import replace

        return Harvester(replace(settings, gitlab_token=""), httpx.MockTransport(self.handler))

    def harvest(self, harvester: Harvester) -> Snapshot:
        self.calls.clear()
        return asyncio.run(harvester.harvest())


def channel_with_mr() -> Channel:
    return Channel(copy.deepcopy(POSTS))


def test_known_mr_is_reread_for_new_reactions(settings):
    channel = channel_with_mr()
    harvester = channel.harvester(settings)
    assert channel.harvest(harvester).mrs[0].emoji_state is EmojiState.APPROVED

    # The post scrolled out of the scanned window, then got merged and a reply.
    channel.posts["root"]["listed"] = channel.posts["reply"]["listed"] = False
    channel.posts["root"]["metadata"]["reactions"].append(
        {"emoji_name": "merged", "user_id": "u_rev", "create_at": NOW}
    )
    channel.posts["reply2"] = mm_post("reply2", "merged!", root_id="root", user="u_author")
    channel.posts["reply2"]["listed"] = False

    mr = channel.harvest(harvester).mrs[0]
    assert "/posts/root/thread" in channel.calls
    assert mr.emoji_state is EmojiState.MERGED
    assert mr.replies == 2


def test_new_mrs_come_from_the_scan_without_rereading(settings):
    channel = channel_with_mr()
    data = channel.harvest(channel.harvester(settings))
    assert len(data.mrs) == 1 and data.mrs[0].replies == 1
    assert not any(c.endswith("/thread") for c in channel.calls)


def test_store_survives_restart(settings):
    channel = channel_with_mr()
    channel.harvest(channel.harvester(settings))
    channel.posts["root"]["listed"] = False
    assert len(channel.harvest(channel.harvester(settings)).mrs) == 1


def test_open_mr_stays_after_harvest_days(settings):
    channel = channel_with_mr()
    harvester = channel.harvester(settings)
    channel.harvest(harvester)
    channel.posts["root"]["create_at"] = NOW - 90 * 86400 * 1000
    harvester._store._db.execute("UPDATE mr_posts SET create_at = 0")
    channel.posts["root"]["listed"] = False
    assert [m.post_id for m in channel.harvest(harvester).mrs] == ["root"]


def test_deleted_post_is_forgotten(settings):
    channel = channel_with_mr()
    harvester = channel.harvester(settings)
    channel.harvest(harvester)
    del channel.posts["root"]
    assert channel.harvest(harvester).mrs == []


def test_finished_mr_is_no_longer_reread(settings):
    channel = channel_with_mr()
    channel.posts["root"]["metadata"]["reactions"].append(
        {"emoji_name": "merged", "user_id": "u_rev", "create_at": NOW}
    )
    harvester = channel.harvester(settings)
    channel.harvest(harvester)  # marks it finished
    channel.posts["root"]["listed"] = False
    data = channel.harvest(harvester)
    assert [m.post_id for m in data.mrs] == ["root"]  # still shown: posted within HARVEST_DAYS
    assert not any(c.endswith("/thread") for c in channel.calls)


def test_merged_by_emoji_but_unknown_to_gitlab_is_still_reread():
    from .test_parse import NO_APP_POST, post

    mr = MergeRequest.from_post(
        post(NO_APP_POST, [{"emoji_name": "merged", "user_id": "u", "create_at": 1}])
    )
    assert mr.finished(gitlab_checked=False)
    assert not mr.finished(gitlab_checked=True)  # GitLab lookup failed: don't retire it yet
