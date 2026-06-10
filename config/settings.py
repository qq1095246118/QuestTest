import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


CONFIG_DIR = Path(__file__).resolve().parent
ENV_FILE = CONFIG_DIR / f"env.{os.getenv('TEST_ENV', 'test')}"


class Settings(BaseSettings):
    """测试环境配置对象。

    请求参数:
        从 config/env.<env> 和环境变量读取接口、账号、DB、SSH 配置。
    返回值:
        pydantic settings 实例，供 API、service 和 pytest fixture 读取运行配置。
    """

    env: str = "test"
    base_url: str = ""

    factor_email: str = ""
    factor_password: str = ""

    factor_db_host: str = ""
    factor_db_port: int = 3306
    factor_db_name: str = ""
    factor_db_user: str = ""
    factor_db_password: str = ""
    factor_webhook_secret: str = ""

    factor_ssh_enabled: bool = False
    factor_ssh_host: str = ""
    factor_ssh_port: int = 22
    factor_ssh_user: str = ""
    factor_ssh_key_path: str = ""
    factor_ssh_password: str = ""

    exchange_test_exchange: str = ""
    exchange_test_account_type: str = ""
    exchange_test_api_key: str = ""
    exchange_test_api_secret: str = ""
    exchange_test_api_passphrase: str = ""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
