import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


CONFIG_DIR = Path(__file__).resolve().parent
ENV_FILE = CONFIG_DIR / f"env.{os.getenv('TEST_ENV', 'test')}"


class Settings(BaseSettings):
    env: str = "test"
    base_url: str = ""
    api_key: str = ""

    factor_email: str = ""
    factor_password: str = ""

    factor_db_host: str = ""
    factor_db_port: int = 3306
    factor_db_name: str = ""
    factor_db_user: str = ""
    factor_db_password: str = ""

    factor_ssh_enabled: bool = False
    factor_ssh_host: str = ""
    factor_ssh_port: int = 22
    factor_ssh_user: str = ""
    factor_ssh_key_path: str = ""
    factor_ssh_password: str = ""

    # Dynamically load config/env.<env> based on the selected test environment.
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
