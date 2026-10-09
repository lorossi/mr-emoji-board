from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from mr_board import main
from mr_board.harvest import MergeRequest, Snapshot

BOARD = Snapshot(
    generated_at=1,
    harvest_seconds=0.1,
    days=30,
    merged_days=7,
    channel="c",
    mm_url="https://mm.test",
    gitlab_checked=False,
    users={},
    mrs=[],
)


@pytest.fixture
def client(monkeypatch, settings):
    async def fake_harvest(self):
        return BOARD

    monkeypatch.setattr(main.Settings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr(main.Harvester, "harvest", fake_harvest)
    with TestClient(main.app) as c:
        c.app.state.board._data = None  # don't depend on the background task's timing
        yield c


def test_board_503_before_first_harvest(client):
    assert client.get("/api/board").status_code == 503


def test_refresh_then_board(client):
    client.app.state.board._last_attempt = 0
    assert client.post("/api/refresh").json()["status"] == "ok"
    body = client.get("/api/board").json()
    assert body["channel"] == "c" and body["error"] is None


def test_refresh_is_rate_limited(client):
    client.app.state.board._last_attempt = 0
    client.post("/api/refresh")
    assert client.post("/api/refresh").status_code == 429


def test_failed_harvest_keeps_previous_snapshot(client, monkeypatch):
    async def boom(self):
        raise RuntimeError("mattermost down")

    board = client.app.state.board
    board._data = BOARD
    monkeypatch.setattr(main.Harvester, "harvest", boom)
    board._last_attempt = 0
    client.post("/api/refresh")
    body = client.get("/api/board").json()
    assert body["channel"] == "c"
    assert "mattermost down" in body["error"]


def test_frontend_is_served(client):
    assert "MR Emoji Board" in client.get("/").text
    assert client.get("/app.js").status_code == 200


def test_board_mrs_include_emoji_state(client):
    from .test_parse import NO_APP_POST, post

    mr = MergeRequest.from_post(post(NO_APP_POST))
    client.app.state.board._data = replace(BOARD, mrs=[mr])
    body = client.get("/api/board").json()
    assert body["mrs"][0]["emoji_state"] == "needs_review"
    assert body["mrs"][0]["gitlab"] is None


def test_health(client):
    assert client.get("/api/health").json() == {"ready": False, "error": None}


def test_oauth_login_redirects_to_mattermost(client):
    resp = client.get("/oauth/login", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"].startswith("https://mm.test/oauth/authorize?")


def test_oauth_callback_rejects_unknown_state(client):
    resp = client.get("/oauth/callback?code=c&state=forged", follow_redirects=False)
    assert resp.status_code == 400


def test_oauth_callback_connects_and_harvests(client, monkeypatch):
    oauth = client.app.state.board.oauth
    connected = []

    async def fake_connect(code, state):
        connected.append((code, state))

    monkeypatch.setattr(oauth, "connect", fake_connect)
    resp = client.get("/oauth/callback?code=c&state=s", follow_redirects=False)
    assert resp.status_code == 303 and connected == [("c", "s")]
    assert client.get("/api/board").json()["channel"] == "c"


def test_oauth_callback_reports_denial(client):
    resp = client.get("/oauth/callback?error=access_denied", follow_redirects=False)
    assert resp.status_code == 400 and "access_denied" in resp.json()["detail"]
