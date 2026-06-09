from __future__ import annotations

from pathlib import Path

from config.settings import Settings


def test_factor_library_settings_load_from_env_file(tmp_path: Path):
    env_file = tmp_path / "env.test"
    env_file.write_text(
        "\n".join(
            [
                "ENV=test",
                "BASE_URL=https://test-factor-backend.questvector.ai",
                "FACTOR_EMAIL=haoran@gmail.com",
                "FACTOR_PASSWORD=Aa%@#haoran",
                "FACTOR_DB_HOST=43.167.190.122",
                "FACTOR_DB_PORT=3306",
                "FACTOR_DB_NAME=factor_db",
                "FACTOR_DB_USER=factor_app",
                "FACTOR_DB_PASSWORD=-RL1Zivb6wIzf4CmqJp6KQ6p",
                "FACTOR_SSH_ENABLED=true",
                "FACTOR_SSH_HOST=43.167.190.122",
                "FACTOR_SSH_PORT=22",
                "FACTOR_SSH_USER=appview",
                "FACTOR_SSH_KEY_PATH=/Users/wrh/.ssh/id_rsa",
                "FACTOR_SSH_PASSWORD=woxiangni.",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.base_url == "https://test-factor-backend.questvector.ai"
    assert settings.factor_email == "haoran@gmail.com"
    assert settings.factor_password == "Aa%@#haoran"
    assert settings.factor_db_host == "43.167.190.122"
    assert settings.factor_db_port == 3306
    assert settings.factor_db_name == "factor_db"
    assert settings.factor_db_user == "factor_app"
    assert settings.factor_db_password == "-RL1Zivb6wIzf4CmqJp6KQ6p"
    assert settings.factor_ssh_enabled is True
    assert settings.factor_ssh_host == "43.167.190.122"
    assert settings.factor_ssh_port == 22
    assert settings.factor_ssh_user == "appview"
    assert settings.factor_ssh_key_path == "/Users/wrh/.ssh/id_rsa"
    assert settings.factor_ssh_password == "woxiangni."
