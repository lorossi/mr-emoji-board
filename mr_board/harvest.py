"""Harvest the tools-reviews Mattermost channel and derive MR states from emoji reactions."""

import asyncio
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Self

import httpx
from pydantic import computed_field

from mr_board.config import Settings
from mr_board.oauth import MattermostOAuth
from mr_board.store import PostStore

# Reaction name -> meaning. Mattermost stores :pencil: as "memo".
REVIEW = {"eyes", "eyesintensify"}
COMMENT = {"memo", "pencil", "pencil2"}
APPROVE = {"white_check_mark", "heavy_check_mark"}
MERGED = {"merged"}
CLOSED = {"x", "no_entry_sign"}

DAY_MS = 86400 * 1000
SCAN_OVERLAP_MS = 10 * 60 * 1000  # re-scan a little before the last scan, for posts saved late


@dataclass(frozen=True)
class Reaction:
    emoji: str
    user_id: str
    at: int

    @classmethod
    def from_mm(cls, raw: dict) -> Self:
        return cls(emoji=raw["emoji_name"], user_id=raw["user_id"], at=raw["create_at"])


class EmojiState(StrEnum):
    NEEDS_REVIEW = "needs_review"
    IN_REVIEW = "in_review"
    COMMENTED = "commented"
    APPROVED = "approved"
    MERGED = "merged"
    CLOSED = "closed"

    @classmethod
    def from_reactions(cls, reactions: list[Reaction]) -> "EmojiState":
        names = [r.emoji for r in reactions]  # already sorted by time
        if MERGED & set(names):
            return cls.MERGED
        if CLOSED & set(names):
            return cls.CLOSED
        # Last progress signal wins: a :pencil: after a :white_check_mark: sends it back to the author.
        for name in reversed(names):
            if name in APPROVE:
                return cls.APPROVED
            if name in COMMENT:
                return cls.COMMENTED
        if REVIEW & set(names):
            return cls.IN_REVIEW
        return cls.NEEDS_REVIEW


@dataclass
class GitLabInfo:
    state: str
    draft: bool
    author: str
    updated_at: str
    merged_at: str | None
    merged_by: str | None
    conflicts: bool
    pipeline: str | None
    approved_by: list[str] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict) -> Self:
        return cls(
            state=data["state"],
            draft=data.get("draft", False),
            author=data["author"]["username"],
            updated_at=data["updated_at"],
            merged_at=data.get("merged_at"),
            merged_by=(data.get("merged_by") or {}).get("username"),
            conflicts=data.get("has_conflicts", False),
            pipeline=(data.get("head_pipeline") or {}).get("status"),
        )

    @property
    def is_final(self) -> bool:
        return self.state in ("merged", "closed")


@dataclass
class MergeRequest:
    post_id: str
    posted_at: int
    author_id: str
    app: str | None
    ticket: str | None
    title: str
    domain: list[str]
    size: str | None
    added: int | None
    removed: int | None
    source: str | None
    target: str | None
    mr_iid: int
    mr_url: str
    project: str
    reactions: list[Reaction] = field(default_factory=list)
    replies: int = 0
    last_reply_at: int | None = None
    repliers: list[str] = field(default_factory=list)
    gitlab: GitLabInfo | None = None

    MR_RE = re.compile(r"\[#MR-(\d+)\]\((https://[^/]+/(.+?)/-/merge_requests/(\d+))\)")
    FIELD_RE = re.compile(r"\*\*(Domain|Size|Branch):\*\*\s*(.+?)\s*$", re.MULTILINE)
    # Drops the "**App:**" prefix, unwraps "[**TICKET**](url)" and unescapes "\_" and friends.
    TITLE_RE = re.compile(r"^\*\*.+?:\*\*\s*|\[\*\*(.+?)\*\*\]\(.+?\)|\\([_*`~])")

    @classmethod
    def from_post(cls, post: dict) -> Self | None:
        msg = post["message"]
        m = cls.MR_RE.search(msg)
        if not m:
            return None

        first = msg.splitlines()[0].strip()
        fields = dict(cls.FIELD_RE.findall(msg))
        ticket = re.search(r"\b([A-Z][A-Z0-9]+-\d+|NO-JIRA)\b", first)
        app = re.match(r"\*\*(.+?):\*\*", first)
        title = cls.TITLE_RE.sub(lambda t: t[1] or t[2] or "", first).strip()
        size = re.match(r"`?([A-Z ]+)`?\s*\(\+(\d+), -(\d+)\)", fields.get("Size", ""))
        branch = re.findall(r"`([^`]+)`", fields.get("Branch", ""))
        reactions = []
        for r in sorted(
            post.get("metadata", {}).get("reactions", []), key=lambda r: r["create_at"]
        ):
            reactions.append(Reaction.from_mm(r))

        return cls(
            post_id=post["id"],
            posted_at=post["create_at"],
            author_id=post["user_id"],
            app=app.group(1) if app else None,
            ticket=ticket.group(1) if ticket else None,
            title=title,
            domain=[d.strip() for d in fields.get("Domain", "").strip("`").split(",") if d.strip()],
            size=size.group(1).strip() if size else None,
            added=int(size.group(2)) if size else None,
            removed=int(size.group(3)) if size else None,
            source=branch[0] if branch else None,
            target=branch[1] if len(branch) > 1 else None,
            mr_iid=int(m.group(4)),
            mr_url=m.group(2),
            project=m.group(3),
            reactions=reactions,
        )

    @computed_field
    @property
    def emoji_state(self) -> EmojiState:
        return EmojiState.from_reactions(self.reactions)

    def finished(self, gitlab_checked: bool) -> bool:
        """Merged or closed by emoji and, when GitLab is checked, by GitLab: no need to re-read it."""
        if self.emoji_state not in (EmojiState.MERGED, EmojiState.CLOSED):
            return False
        return not gitlab_checked or (self.gitlab is not None and self.gitlab.is_final)

    @property
    def user_ids(self) -> set[str]:
        return {self.author_id, *self.repliers, *(r.user_id for r in self.reactions)}

    def attach_thread(self, thread: list[dict]) -> None:
        thread = sorted(thread, key=lambda r: r["create_at"])
        self.replies = len(thread)
        self.last_reply_at = thread[-1]["create_at"] if thread else None
        self.repliers = list(dict.fromkeys(r["user_id"] for r in thread))


@dataclass
class Snapshot:
    generated_at: int
    harvest_seconds: float
    days: int
    merged_days: int  # how long merged MRs stay on the board
    channel: str
    mm_url: str  # for links back to the posts
    gitlab_checked: bool
    users: dict[str, str]
    mrs: list[MergeRequest]


@dataclass
class ChannelHarvest:
    """What the Mattermost channel yields: the MR posts and the names of everyone involved."""

    mrs: list[MergeRequest]
    users: dict[str, str]  # user id -> username

    def get_mrs(self) -> list[MergeRequest]:
        """The MRs, newest post first."""
        return sorted(self.mrs, key=lambda m: m.posted_at, reverse=True)


class MattermostClient:
    def __init__(
        self,
        settings: Settings,
        auth: httpx.Auth,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.s = settings
        self.auth = auth
        self.transport = transport
        self._reread_slots = asyncio.Semaphore(8)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{self.s.mm_url}/api/v4",
            auth=self.auth,
            timeout=30,
            transport=self.transport,
        )

    async def fetch_merge_requests(self, store: PostStore) -> ChannelHarvest:
        """Return the channel's MR posts, with their threads, and who is involved.

        Only posts since the last scan are read from the channel; MRs found earlier and not
        finished yet are re-read one by one, for their current reactions and replies.
        """
        now = int(time.time() * 1000)
        last_scan = store.last_scan()
        if last_scan is None:  # first run: fill the store
            cutoff = now - self.s.harvest_days * DAY_MS
        else:
            cutoff = last_scan - SCAN_OVERLAP_MS

        async with self._client() as mm:
            scanned = await self._scan(mm, cutoff)
            # Replies come after their post, so the scan holds every reply of a post it found.
            for root, replies in scanned.values():
                store.save(root, replies)
            older = [i for i in store.active_ids() if i not in scanned]
            await asyncio.gather(*(self._reread(mm, store, i) for i in older))
            store.set_last_scan(now)

            mrs = []
            for root, replies in store.load(since=now - self.s.harvest_days * DAY_MS):
                if mr := MergeRequest.from_post(root):
                    mr.attach_thread(replies)
                    mrs.append(mr)
            users = await self._usernames(mm, set().union(*(mr.user_ids for mr in mrs)))
        return ChannelHarvest(mrs, users)

    async def _scan(self, mm: httpx.AsyncClient, cutoff: int) -> dict[str, tuple[dict, list[dict]]]:
        """The MR posts created since `cutoff` (ms), with their replies, by post id."""
        posts = await self._fetch_posts(mm, cutoff)
        replies: dict[str, list[dict]] = {}
        for p in posts.values():
            if p["root_id"]:
                replies.setdefault(p["root_id"], []).append(p)
        return {
            p["id"]: (p, replies.get(p["id"], []))
            for p in posts.values()
            if not p["root_id"] and MergeRequest.from_post(p)
        }

    async def _reread(self, mm: httpx.AsyncClient, store: PostStore, post_id: str) -> None:
        """Refresh a known MR post and its replies; forget it if it was deleted."""
        async with self._reread_slots:
            resp = await mm.get(f"/posts/{post_id}/thread")
        if resp.status_code in (403, 404):  # deleted, or the channel became unreadable for it
            store.delete(post_id)
            return
        resp.raise_for_status()
        posts = resp.json()["posts"]
        root = posts.pop(post_id)
        store.save(root, [p for p in posts.values() if not p.get("delete_at")])

    async def _fetch_posts(self, mm: httpx.AsyncClient, cutoff: int) -> dict:
        posts, page = {}, 0
        while True:
            resp = await mm.get(
                f"/channels/{self.s.mm_channel_id}/posts",
                params={"per_page": 200, "page": page},
            )
            resp.raise_for_status()
            batch = resp.json()
            order = batch.get("order", [])
            if not order:
                break
            for pid in order:
                posts[pid] = batch["posts"][pid]
            if min(batch["posts"][p]["create_at"] for p in order) < cutoff:
                break
            page += 1
        return {
            k: v for k, v in posts.items() if v["create_at"] >= cutoff and not v.get("delete_at")
        }

    async def _usernames(self, mm: httpx.AsyncClient, ids: set[str]) -> dict[str, str]:
        resp = await mm.post("/users/ids", json=sorted(ids))
        resp.raise_for_status()
        return {u["id"]: u["username"] for u in resp.json()}


class GitLabClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._settings = settings
        self._transport = transport
        self._final_cache: dict[str, GitLabInfo] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._settings.gitlab_token)

    async def enrich(self, mrs: list[MergeRequest]) -> None:
        """Set `gitlab` on each MR (None when GitLab could not tell us about it)."""
        sem = asyncio.Semaphore(8)
        async with httpx.AsyncClient(
            base_url=f"{self._settings.gitlab_url}/api/v4",
            headers={"PRIVATE-TOKEN": self._settings.gitlab_token},
            timeout=30,
            transport=self._transport,
        ) as gl:
            infos = await asyncio.gather(*(self._info(gl, sem, mr) for mr in mrs))
        for mr, info in zip(mrs, infos):
            mr.gitlab = info

    async def _info(
        self,
        gl: httpx.AsyncClient,
        sem: asyncio.Semaphore,
        mr: MergeRequest,
    ) -> GitLabInfo | None:
        if mr.mr_url in self._final_cache:
            return self._final_cache[mr.mr_url]

        base = f"/projects/{urllib.parse.quote(mr.project, safe='')}/merge_requests/{mr.mr_iid}"
        async with sem:
            resp = await gl.get(base)
            if resp.status_code != 200:
                return None
            info = GitLabInfo.from_api(resp.json())
            if info.state != "opened":
                return info

            appr = await gl.get(f"{base}/approvals")
            if appr.status_code != 200:
                return info

            info.approved_by = [a["user"]["username"] for a in appr.json()["approved_by"]]

        if info.is_final:
            self._final_cache[mr.mr_url] = info

        return info


class Harvester:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        auth: httpx.Auth | None = None,
    ):
        self._settings = settings
        self._mattermost = MattermostClient(
            settings,
            auth or MattermostOAuth(settings),
            transport,
        )  # transport injectable for tests
        self.gitlab = GitLabClient(settings, transport)
        self._store = PostStore(settings.state_dir / "board.db")

    async def harvest(self) -> Snapshot:
        started = time.time()
        channel = await self._mattermost.fetch_merge_requests(self._store)
        requests = channel.get_mrs()
        if self.gitlab.enabled:
            await self.gitlab.enrich(requests)
        self._store.mark_done([mr.post_id for mr in requests if mr.finished(self.gitlab.enabled)])

        return Snapshot(
            generated_at=int(time.time() * 1000),
            harvest_seconds=round(time.time() - started, 1),
            days=self._settings.harvest_days,
            merged_days=self._settings.merged_days,
            channel=self._settings.mm_channel_name,
            mm_url=self._settings.mm_url,
            gitlab_checked=self.gitlab.enabled,
            users=channel.users,
            mrs=requests,
        )
