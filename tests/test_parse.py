import pytest
from pydantic import TypeAdapter

from mr_board.harvest import EmojiState, MergeRequest, Reaction

APP_POST = (
    "**FGC Device Manager:** [**FGCDEVMAN-318**](https://its.cern.ch/jira/browse/FGCDEVMAN-318) "
    "Python ty cleanup for fgc\\_device\\_manager  \n"
    "**Domain:** `DEV-OPS, BACK-END`  \n"
    "**Size:** `VERY LARGE` (+799, -642)  \n"
    "**Branch:** `dev/kekessle/fgcdevman-322-ty-cleanup` ➔ `sprint/epctools-2026-09-ruffty`  \n"
    "**Merge Request:** [#MR-1891](https://gitlab.cern.ch/epc/ccs/tools/fgc-web/-/merge_requests/1891)"
)
NO_APP_POST = (
    "NO-JIRA fix behaviour in exception catch  \n"
    "**Domain:** `BACK-END`  \n"
    "**Size:** `SMALL` (+8, -3)  \n"
    "**Branch:** `dev/rossilo/no-jira-fix` ➔ `master`  \n"
    "**Merge Request:** [#MR-933](https://gitlab.cern.ch/epc/ccs/fgc/fgc/-/merge_requests/933)"
)


def post(message: str, reactions: list[dict] | None = None) -> dict:
    return {
        "id": "p1",
        "create_at": 1000,
        "user_id": "u1",
        "message": message,
        "metadata": {"reactions": reactions or []},
    }


def test_parse_post_with_app_prefix():
    mr = MergeRequest.from_post(post(APP_POST))
    assert mr.app == "FGC Device Manager"
    assert mr.ticket == "FGCDEVMAN-318"
    assert mr.title == "FGCDEVMAN-318 Python ty cleanup for fgc_device_manager"
    assert mr.domain == ["DEV-OPS", "BACK-END"]
    assert (mr.size, mr.added, mr.removed) == ("VERY LARGE", 799, 642)
    assert mr.source == "dev/kekessle/fgcdevman-322-ty-cleanup"
    assert mr.target == "sprint/epctools-2026-09-ruffty"
    assert mr.project == "epc/ccs/tools/fgc-web"
    assert mr.mr_iid == 1891


def test_parse_post_without_app_prefix():
    mr = MergeRequest.from_post(post(NO_APP_POST))
    assert mr.app is None
    assert mr.ticket == "NO-JIRA"
    assert mr.title == "NO-JIRA fix behaviour in exception catch"
    assert mr.project == "epc/ccs/fgc/fgc"
    assert mr.mr_iid == 933


def test_non_mr_post_is_ignored():
    assert MergeRequest.from_post(post("lgtm 👍")) is None


def test_reactions_are_sorted_by_time():
    raw = [
        {"emoji_name": "white_check_mark", "user_id": "u2", "create_at": 20},
        {"emoji_name": "eyes", "user_id": "u2", "create_at": 10},
    ]
    mr = MergeRequest.from_post(post(APP_POST, raw))
    assert mr.reactions == [Reaction("eyes", "u2", 10), Reaction("white_check_mark", "u2", 20)]
    assert mr.emoji_state is EmojiState.APPROVED
    assert mr.user_ids == {"u1", "u2"}


def test_serialisation_includes_emoji_state():
    mr = MergeRequest.from_post(post(NO_APP_POST))
    d = TypeAdapter(MergeRequest).dump_python(mr, mode="json")
    assert d["emoji_state"] == "needs_review"
    assert d["reactions"] == [] and d["gitlab"] is None


def rx(*names: str) -> list[Reaction]:
    return [Reaction(emoji=n, user_id="u", at=i) for i, n in enumerate(names)]


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        ((), "needs_review"),
        (("partying_face",), "needs_review"),
        (("eyes",), "in_review"),
        (("eyes", "memo"), "commented"),
        (("eyes", "white_check_mark"), "approved"),
        (("eyes", "white_check_mark", "memo"), "commented"),  # new comments after approval
        (("eyes", "memo", "white_check_mark"), "approved"),
        (("white_check_mark", "merged"), "merged"),
        (("merged", "memo"), "merged"),  # merged is final
        (("eyes", "x"), "closed"),
    ],
)
def test_emoji_state(names, expected):
    assert EmojiState.from_reactions(rx(*names)) == expected
