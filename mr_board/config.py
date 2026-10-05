import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

SECRETS_DIR = Path("/run/secrets")  # Docker secrets
LOCAL_SECRETS_DIR = Path(__file__).resolve().parent.parent / "secrets"  # same files, no Docker
# Non-secret settings; real environment variables override it.
CONFIG_FILE = Path(
    os.environ.get(
        "MR_BOARD_CONFIG",
        Path(__file__).resolve().parent.parent / "settings.env",
    )
)


@dataclass(frozen=True)
class Settings:
    mm_url: str
    mm_token: str
    mm_channel_id: str
    mm_channel_name: str
    gitlab_url: str
    gitlab_token: str
    harvest_days: int
    refresh_minutes: float
    merged_days: int

    @classmethod
    def _read_secret(cls, name: str) -> str | None:
        """Read secret NAME from /run/secrets/name, then ./secrets/name, then $NAME."""
        for directory in (SECRETS_DIR, LOCAL_SECRETS_DIR):
            secret_file = directory / name.lower()
            if secret_file.is_file():
                return secret_file.read_text().strip()
        return os.environ.get(name)

    @classmethod
    def from_env(cls) -> "Settings":
        mm_token = cls._read_secret("MM_TOKEN")
        if mm_token is None:
            raise RuntimeError(
                "MM_TOKEN not found in /run/secrets/mm_token, secrets/mm_token or the environment"
            )
        glab_token = cls._read_secret("GITLAB_TOKEN")
        if glab_token is None:
            raise RuntimeError(
                "GITLAB_TOKEN not found in /run/secrets/gitlab_token, secrets/gitlab_token or the environment"
            )

        config = {**dotenv_values(CONFIG_FILE), **os.environ}

        def setting(name: str) -> str:
            if not (value := config.get(name)):
                raise RuntimeError(
                    f"{name} is not set in {CONFIG_FILE} or the environment"
                )
            return value

        return cls(
            mm_url=setting("MM_URL").rstrip("/"),
            mm_token=mm_token,
            mm_channel_id=setting("MM_CHANNEL_ID"),
            mm_channel_name=setting("MM_CHANNEL_NAME"),
            gitlab_url=setting("GITLAB_URL").rstrip("/"),
            gitlab_token=glab_token,
            harvest_days=int(setting("HARVEST_DAYS")),
            refresh_minutes=float(setting("REFRESH_MINUTES")),
            merged_days=int(setting("MERGED_DAYS")),
        )
