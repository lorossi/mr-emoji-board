"""Mattermost access through an OAuth 2.0 app: a one-off browser login, then refreshed tokens."""

import json
import secrets
import time
import urllib.parse
from dataclasses import asdict, dataclass

import httpx

from mr_board.config import Settings

TOKEN_FILE = "mattermost_oauth.json"
EXPIRY_MARGIN = 60  # seconds: refresh a bit before Mattermost would refuse the token


class NotConnected(RuntimeError):
    """Nobody has authorised the board in Mattermost yet, or the authorisation was revoked."""

    def __init__(self, reason: str = "Not connected to Mattermost yet"):
        super().__init__(f"{reason}: open /oauth/login to connect")


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: float  # unix seconds

    @classmethod
    def from_response(cls, response: httpx.Response) -> "Tokens":
        body = response.json()
        return cls(
            access_token=body["access_token"],
            refresh_token=body["refresh_token"],
            expires_at=time.time() + int(body.get("expires_in", 0)),
        )

    @property
    def expired(self) -> bool:
        return time.time() > self.expires_at - EXPIRY_MARGIN


class MattermostOAuth(httpx.Auth):
    """Bearer auth with the stored OAuth access token, refreshed when it expires.

    Mattermost hands out a new refresh token on every refresh and revokes the old one,
    so the tokens are written back to the state directory each time.
    """

    requires_response_body = True  # token responses are read inside the auth flow

    def __init__(self, settings: Settings):
        self.s = settings
        self._file = settings.state_dir / TOKEN_FILE
        self._pending_states: set[str] = set()
        self._tokens = self._load()

    @property
    def connected(self) -> bool:
        return self._tokens is not None

    def authorize_url(self) -> str:
        state = secrets.token_urlsafe(16)
        self._pending_states.add(state)
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": self.s.mm_client_id,
                "redirect_uri": self.s.mm_redirect_url,
                "state": state,
            }
        )
        return f"{self.s.mm_url}/oauth/authorize?{query}"

    async def connect(
        self, code: str, state: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        """Finish the browser login: trade the callback's code for tokens."""
        if state not in self._pending_states:
            raise ValueError("Unknown or reused OAuth state, start again from /oauth/login")
        self._pending_states.discard(state)
        async with httpx.AsyncClient(timeout=30, transport=transport) as client:
            response = await client.send(
                self._token_request(grant_type="authorization_code", code=code)
            )
        self._store(self._check(response, "Authorisation"))

    def auth_flow(self, request: httpx.Request):
        if self._tokens is None:
            raise NotConnected()
        if self._tokens.expired:
            yield from self._refresh()
        request.headers["Authorization"] = f"Bearer {self._tokens.access_token}"
        response = yield request
        if response.status_code == 401:
            yield from self._refresh()
            request.headers["Authorization"] = f"Bearer {self._tokens.access_token}"
            yield request

    def _refresh(self):
        response = yield self._token_request(
            grant_type="refresh_token", refresh_token=self._tokens.refresh_token
        )
        if response.status_code in (400, 401, 403):  # refresh token revoked or expired
            self._store(None)
            raise NotConnected("Mattermost refused to renew the board's access")
        self._store(self._check(response, "Token refresh"))

    def _token_request(self, **form: str) -> httpx.Request:
        return httpx.Request(
            "POST",
            f"{self.s.mm_url}/oauth/access_token",
            data={
                "client_id": self.s.mm_client_id,
                "client_secret": self.s.mm_client_secret,
                "redirect_uri": self.s.mm_redirect_url,
                **form,
            },
        )

    @staticmethod
    def _check(response: httpx.Response, what: str) -> Tokens:
        if response.status_code != 200:
            try:
                reason = response.json().get("message", response.text)
            except ValueError:
                reason = response.text
            raise RuntimeError(f"{what} failed ({response.status_code}): {reason}")
        return Tokens.from_response(response)

    def _load(self) -> Tokens | None:
        if not self._file.is_file():
            return None
        return Tokens(**json.loads(self._file.read_text()))

    def _store(self, tokens: Tokens | None) -> None:
        self._tokens = tokens
        if tokens is None:
            self._file.unlink(missing_ok=True)
            return
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        tmp.touch(mode=0o600)
        tmp.write_text(json.dumps(asdict(tokens)))
        tmp.replace(self._file)
