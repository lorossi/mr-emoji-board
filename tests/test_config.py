from pathlib import Path

import pytest

from mr_board import config
from mr_board.config import Settings

SETTINGS = (
    "MM_URL",
    "MM_CHANNEL_ID",
    "MM_CHANNEL_NAME",
    "MM_OAUTH_CLIENT_ID",
    "MM_OAUTH_REDIRECT_URL",
    "STATE_DIR",
    "GITLAB_URL",
    "HARVEST_DAYS",
    "REFRESH_MINUTES",
    "MERGED_DAYS",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for var in ("MM_CLIENT_SECRET", "GITLAB_TOKEN", *SETTINGS):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / "run-secrets")


@pytest.fixture
def config_file(monkeypatch, tmp_path):
    f = tmp_path / "settings.env"
    f.write_text(
        "# comment\n"
        "MM_URL=https://mm.example/\n"
        "MM_CHANNEL_ID=chan\n"
        "MM_CHANNEL_NAME=reviews\n"
        "MM_OAUTH_CLIENT_ID=client\n"
        "MM_OAUTH_REDIRECT_URL=http://localhost:8000/oauth/callback\n"
        "STATE_DIR=/var/board\n"
        "GITLAB_URL=https://gitlab.example/\n"
        "HARVEST_DAYS=7\n"
        "REFRESH_MINUTES=0.5\n"
        "MERGED_DAYS=3\n"
    )
    monkeypatch.setattr(config, "CONFIG_FILE", f)
    return f


@pytest.fixture
def config_secrets(monkeypatch, tmp_path):
    secrets = tmp_path / "run-secrets"
    secrets.mkdir()
    (secrets / "mm_client_secret").write_text("mm-secret\n")
    (secrets / "gitlab_token").write_text("gl-secret\n")
    monkeypatch.setattr(config, "SECRETS_DIR", secrets)
    return secrets


def test_settings_come_from_config_file(config_file, config_secrets):
    s = Settings.from_env()
    assert s.mm_url == "https://mm.example"
    assert s.gitlab_url == "https://gitlab.example"
    assert (s.mm_channel_id, s.mm_channel_name) == ("chan", "reviews")
    assert (s.harvest_days, s.refresh_minutes, s.merged_days) == (7, 0.5, 3)
    assert (s.mm_client_id, s.mm_client_secret) == ("client", "mm-secret")
    assert s.mm_redirect_url == "http://localhost:8000/oauth/callback"
    assert s.state_dir == Path("/var/board")
    assert s.gitlab_token == "gl-secret"


def test_env_var_overrides_config_file(config_file, config_secrets, monkeypatch):
    monkeypatch.setenv("HARVEST_DAYS", "30")
    assert Settings.from_env().harvest_days == 30


def test_missing_setting_raises(config_file, config_secrets):
    config_file.write_text("MM_URL=https://mm.example\n")
    with pytest.raises(RuntimeError, match="MM_CHANNEL_ID"):
        Settings.from_env()


def test_repo_settings_file_is_complete(config_secrets):
    assert config.CONFIG_FILE.is_file()
    assert Settings.from_env().mm_channel_name == "tools-reviews"


def test_missing_mm_client_secret_raises(config_file, config_secrets):
    (config_secrets / "mm_client_secret").unlink()
    with pytest.raises(RuntimeError, match="MM_CLIENT_SECRET"):
        Settings.from_env()


def test_env_secret_is_ignored(config_file, config_secrets, monkeypatch):
    monkeypatch.setenv("MM_CLIENT_SECRET", "mm-env")
    assert Settings.from_env().mm_client_secret == "mm-secret"


def test_relative_state_dir_is_in_the_repo(config_secrets):
    assert Settings.from_env().state_dir == config.REPO_DIR / "state"
