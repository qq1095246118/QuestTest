import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


CONFIG_DIR = Path(__file__).resolve().parent
ENV_FILE = CONFIG_DIR / f".env.{os.getenv('TEST_ENV', 'test')}"

class Settings(BaseSettings):
    env: str = "test"
    base_url: str = ""
    api_key: str = ""
    
    # Binance Base URLs
    binance_spot_url: str = "https://api.binance.com"
    binance_usdm_url: str = "https://fapi.binance.com"
    binance_coinm_url: str = "https://dapi.binance.com"
    
    # 数据库配置
    db_host: str = ""
    db_port: int = 3306
    db_user: str = ""
    db_password: str = ""
    db_name: str = ""

    # Dynamically load .env file based on environment variable
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding='utf-8',
        extra='ignore'
    )

settings = Settings()
