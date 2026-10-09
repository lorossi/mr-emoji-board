import asyncio
import json
import urllib.parse

import httpx
import pytest

from mr_board.oauth import TOKEN_FILE, MattermostOAuth, NotConnected


class FakeMattermost:
    """Hands out numbered tokens and only accepts the latest access token."""

    def __init__(self):
        self.issued = 0
        self.token_requests: list[dict] = []
        self.refresh_ok = True

    def _tokens(self) -> httpx.Response:
        self.issued += 1
        return httpx.Response(
            200,
            json={
                "access_token": f"access-{self.issued}",
                "refresh_token": f"refresh-{self.issued}",
                "expires_in": 3600,
                "token_type": "bearer",
            },
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/access_token":
            form = dict(urllib.parse.parse_qsl(request.content.decode()))
            self.token_requests.append(form)
            if form["grant_type"] == "authorization_code" and form["code"] == "good-code":
                return self._tokens()
            if form["grant_type"] == "refresh_token" and self.refresh_ok:
                return self._tokens()
            return httpx.Response(400, json={"message": "invalid_grant"})
        if request.headers.get("Authorization") == f"Bearer access-{self.issued}":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(401, json={"message": "Invalid or expired session"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture
def fake() -> FakeMattermost:
    return FakeMattermost()


@pytest.fixture
def oauth(settings) -> MattermostOAuth:
    (settings.state_dir / TOKEN_FILE).unlink()
    return MattermostOAuth(settings)


def get(oauth: MattermostOAuth, fake: FakeMattermost) -> httpx.Response:
    async def run():
        async with httpx.AsyncClient(auth=oauth, transport=fake.transport()) as client:
            return await client.get("https://mm.test/api/v4/users/me")

    return asyncio.run(run())


def connect(oauth: MattermostOAuth, fake: FakeMattermost, code="good-code") -> None:
    state = urllib.parse.parse_qs(urllib.parse.urlparse(oauth.authorize_url()).query)["state"][0]
    asyncio.run(oauth.connect(code, state, fake.transport()))


def test_authorize_url(oauth):
    url = urllib.parse.urlparse(oauth.authorize_url())
    query = urllib.parse.parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://mm.test/oauth/authorize"
    assert query["client_id"] == ["client"]
    assert query["redirect_uri"] == ["http://localhost:8000/oauth/callback"]
    assert query["response_type"] == ["code"]


def test_requests_fail_until_connected(oauth, fake):
    with pytest.raises(NotConnected, match="/oauth/login"):
        get(oauth, fake)


def test_connect_stores_tokens(oauth, fake, settings):
    connect(oauth, fake)
    assert get(oauth, fake).status_code == 200
    assert fake.token_requests[0]["client_secret"] == "client-secret"
    stored = json.loads((settings.state_dir / TOKEN_FILE).read_text())
    assert stored["refresh_token"] == "refresh-1"
    assert MattermostOAuth(settings).connected  # survives a restart


def test_unknown_state_is_rejected(oauth, fake):
    with pytest.raises(ValueError, match="state"):
        asyncio.run(oauth.connect("good-code", "forged", fake.transport()))


def test_rejected_code_raises(oauth, fake):
    with pytest.raises(RuntimeError, match="Authorisation failed \\(400\\): invalid_grant"):
        connect(oauth, fake, code="bad-code")
    assert not oauth.connected


def test_expired_token_is_refreshed_first(oauth, fake, settings):
    connect(oauth, fake)
    oauth._tokens.expires_at = 0
    assert get(oauth, fake).status_code == 200
    assert fake.token_requests[-1]["refresh_token"] == "refresh-1"
    stored = json.loads((settings.state_dir / TOKEN_FILE).read_text())
    assert stored["refresh_token"] == "refresh-2"


def test_401_triggers_refresh_and_retry(oauth, fake):
    connect(oauth, fake)
    fake.issued += 1  # Mattermost no longer accepts access-1
    assert get(oauth, fake).status_code == 200
    assert fake.token_requests[-1]["grant_type"] == "refresh_token"


def test_revoked_refresh_token_disconnects(oauth, fake, settings):
    connect(oauth, fake)
    oauth._tokens.expires_at = 0
    fake.refresh_ok = False
    with pytest.raises(NotConnected, match="refused"):
        get(oauth, fake)
    assert not (settings.state_dir / TOKEN_FILE).exists()
