import json

import pytest

from mr_board.config import Settings
from mr_board.oauth import TOKEN_FILE


@pytest.fixture
def settings(tmp_path) -> Settings:
    state = tmp_path / "state"
    state.mkdir()
    (state / TOKEN_FILE).write_text(
        json.dumps({"access_token": "mm-token", "refresh_token": "mm-refresh", "expires_at": 2e9})
    )
    return Settings(
        mm_url="https://mm.test",
        mm_client_id="client",
        mm_client_secret="client-secret",
        mm_redirect_url="http://localhost:8000/oauth/callback",
        mm_channel_id="chan",
        mm_channel_name="tools-reviews",
        gitlab_url="https://gitlab.test",
        gitlab_token="gl-token",
        harvest_days=30,
        refresh_minutes=5,
        merged_days=7,
        state_dir=state,
    )
