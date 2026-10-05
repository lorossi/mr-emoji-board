import pytest

from mr_board import config
from mr_board.config import Settings

SETTINGS = (
    "MM_URL",
    "MM_CHANNEL_ID",
    "MM_CHANNEL_NAME",
    "GITLAB_URL",
    "HARVEST_DAYS",
    "REFRESH_MINUTES",
    "MERGED_DAYS",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for var in ("MM_TOKEN", "GITLAB_TOKEN", *SETTINGS):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / "run-secrets")
    monkeypatch.setattr(config, "LOCAL_SECRETS_DIR", tmp_path / "local-secrets")
    monkeypatch.setenv("MM_TOKEN", "mm")
    monkeypatch.setenv("GITLAB_TOKEN", "gl")


@pytest.fixture
def config_file(monkeypatch, tmp_path):
    f = tmp_path / "settings.env"
    f.write_text(
        "# comment\n"
        "MM_URL=https://mm.example/\n"
        "MM_CHANNEL_ID=chan\n"
        "MM_CHANNEL_NAME=reviews\n"
        "GITLAB_URL=https://gitlab.example/\n"
        "HARVEST_DAYS=7\n"
        "REFRESH_MINUTES=0.5\n"
        "MERGED_DAYS=3\n"
    )
    monkeypatch.setattr(config, "CONFIG_FILE", f)
    return f


def test_settings_come_from_config_file(config_file):
    s = Settings.from_env()
    assert s.mm_url == "https://mm.example"
    assert s.gitlab_url == "https://gitlab.example"
    assert (s.mm_channel_id, s.mm_channel_name) == ("chan", "reviews")
    assert (s.harvest_days, s.refresh_minutes, s.merged_days) == (7, 0.5, 3)


def test_env_var_overrides_config_file(config_file, monkeypatch):
    monkeypatch.setenv("HARVEST_DAYS", "30")
    assert Settings.from_env().harvest_days == 30


def test_missing_setting_raises(config_file):
    config_file.write_text("MM_URL=https://mm.example\n")
    with pytest.raises(RuntimeError, match="MM_CHANNEL_ID"):
        Settings.from_env()


def test_repo_settings_file_is_complete():
    assert config.CONFIG_FILE.is_file()
    assert Settings.from_env().mm_channel_name == "tools-reviews"


def test_missing_mm_token_raises(config_file, monkeypatch):
    monkeypatch.delenv("MM_TOKEN")
    with pytest.raises(RuntimeError, match="MM_TOKEN"):
        Settings.from_env()


def test_secrets_dir_wins_over_env(config_file, tmp_path):
    secrets = tmp_path / "run-secrets"
    secrets.mkdir()
    (secrets / "mm_token").write_text("mm-secret\n")
    assert Settings.from_env().mm_token == "mm-secret"


def test_local_secrets_dir_is_used(config_file, tmp_path):
    local = tmp_path / "local-secrets"
    local.mkdir()
    (local / "gitlab_token").write_text("gl-local\n")
    assert Settings.from_env().gitlab_token == "gl-local"
