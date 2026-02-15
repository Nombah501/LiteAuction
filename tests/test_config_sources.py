from __future__ import annotations

from pathlib import Path

from app.config import Settings


def _write_toml(path: Path, *, bid_cooldown_seconds: int) -> None:
    path.write_text(f"bid_cooldown_seconds = {bid_cooldown_seconds}\n", encoding="utf-8")


def test_settings_load_from_toml_file_via_app_config_file_env(monkeypatch, tmp_path: Path) -> None:
    toml_path = tmp_path / "settings.toml"
    _write_toml(toml_path, bid_cooldown_seconds=17)

    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("APP_CONFIG_FILE", str(toml_path))
    monkeypatch.delenv("BID_COOLDOWN_SECONDS", raising=False)

    settings = Settings(_env_file=None)

    assert settings.bid_cooldown_seconds == 17


def test_environment_overrides_toml_defaults(monkeypatch, tmp_path: Path) -> None:
    toml_path = tmp_path / "settings.toml"
    _write_toml(toml_path, bid_cooldown_seconds=17)

    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("APP_CONFIG_FILE", str(toml_path))
    monkeypatch.setenv("BID_COOLDOWN_SECONDS", "9")

    settings = Settings(_env_file=None)

    assert settings.bid_cooldown_seconds == 9


def test_dotenv_overrides_toml_defaults(monkeypatch, tmp_path: Path) -> None:
    toml_path = tmp_path / "settings.toml"
    dotenv_path = tmp_path / "test.env"
    _write_toml(toml_path, bid_cooldown_seconds=17)
    dotenv_path.write_text("BOT_TOKEN=dotenv-token\nBID_COOLDOWN_SECONDS=11\n", encoding="utf-8")

    monkeypatch.setenv("APP_CONFIG_FILE", str(toml_path))
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("BID_COOLDOWN_SECONDS", raising=False)

    settings = Settings(_env_file=dotenv_path)

    assert settings.bid_cooldown_seconds == 11


def test_default_toml_path_is_used_when_no_override(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    _write_toml(config_dir / "defaults.toml", bid_cooldown_seconds=14)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.delenv("APP_CONFIG_FILE", raising=False)
    monkeypatch.delenv("BID_COOLDOWN_SECONDS", raising=False)

    settings = Settings(_env_file=None)

    assert settings.bid_cooldown_seconds == 14
