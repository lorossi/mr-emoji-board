import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

SECRETS_DIR = Path("/run/secrets")  # Docker secrets
REPO_DIR = Path(__file__).resolve().parent.parent
# Non-secret settings; real environment variables override it.
CONFIG_FILE = Path(
    os.environ.get(
        "MR_BOARD_CONFIG",
        REPO_DIR / "settings.env",
    )
)


@dataclass(frozen=True)
class Settings:
    mm_url: str
    mm_client_id: str  # the board's OAuth 2.0 app in Mattermost
    mm_client_secret: str
    mm_redirect_url: str
    mm_channel_id: str
    mm_channel_name: str
    gitlab_url: str
    gitlab_token: str
    harvest_days: int
    refresh_minutes: float
    merged_days: int
    state_dir: Path  # where the Mattermost OAuth tokens are kept

    @classmethod
    def _read_secret(cls, name: str) -> str | None:
        """Read secret NAME from /run/secrets/name."""
        secret_file = SECRETS_DIR / name.lower()
        if not secret_file.is_file():
            return None
        return secret_file.read_text().strip()

    @classmethod
    def from_env(cls) -> "Settings":
        mm_client_secret = cls._read_secret("MM_CLIENT_SECRET")
        if mm_client_secret is None:
            raise RuntimeError("MM_CLIENT_SECRET not found in /run/secrets/mm_client_secret")
        glab_token = cls._read_secret("GITLAB_TOKEN")
        if glab_token is None:
            raise RuntimeError("GITLAB_TOKEN not found in /run/secrets/gitlab_token")

        config = {**dotenv_values(CONFIG_FILE), **os.environ}

        def setting(name: str) -> str:
            if not (value := config.get(name)):
                raise RuntimeError(f"{name} is not set in {CONFIG_FILE} or the environment")
            return value

        return cls(
            mm_url=setting("MM_URL").rstrip("/"),
            mm_channel_id=setting("MM_CHANNEL_ID"),
            mm_channel_name=setting("MM_CHANNEL_NAME"),
            mm_client_id=setting("MM_OAUTH_CLIENT_ID"),
            mm_client_secret=mm_client_secret,
            mm_redirect_url=setting("MM_OAUTH_REDIRECT_URL"),
            gitlab_url=setting("GITLAB_URL").rstrip("/"),
            gitlab_token=glab_token,
            harvest_days=int(setting("HARVEST_DAYS")),
            refresh_minutes=float(setting("REFRESH_MINUTES")),
            merged_days=int(setting("MERGED_DAYS")),
            state_dir=REPO_DIR / setting("STATE_DIR"),  # relative paths are relative to the repo
        )
