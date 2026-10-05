import pytest

from mr_board.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        mm_url="https://mm.test",
        mm_token="mm-token",
        mm_channel_id="chan",
        mm_channel_name="tools-reviews",
        gitlab_url="https://gitlab.test",
        gitlab_token="gl-token",
        harvest_days=30,
        refresh_minutes=5,
        merged_days=7,
    )
