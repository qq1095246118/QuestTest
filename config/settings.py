import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


CONFIG_DIR = Path(__file__).resolve().parent
ENV_FILE = CONFIG_DIR / f"env.{os.getenv('TEST_ENV', 'test')}"

class Settings(BaseSettings):
    env: str = "test"
    base_url: str = ""
    api_key: str = ""

    # Dynamically load config/env.<env> based on the selected test environment.
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding='utf-8',
        extra='ignore'
    )

settings = Settings()
