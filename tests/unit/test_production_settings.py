"""独立 production 本机覆盖、运行时优先级与只读边界的离线测试。"""

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from config.settings import SettingsLoader

pytestmark = pytest.mark.unit


@pytest.fixture
def synthetic_config(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, Any]]:
    """替换配置读取为合成对象；返回可调整配置树，不读取任何本机凭据。"""
    for variable in SettingsLoader._ENVIRONMENT_OVERRIDES:
        monkeypatch.delenv(variable, raising=False)
    sections = ("api", "authentication", "database", "factor_combo", "factor_data", "environment_safety", "reports")
    files = {
        "default.yaml": {name: {} for name in sections},
        "test.yaml": {"factor_data": {"auth_token": "synthetic-test-token"}},
        "production.yaml": {"environment_safety": {"allowed_hosts": ["factor-frontend.questvector.ai"]}},
        "production.local.yaml": {
            "factor_data": {"auth_token": "synthetic-production-token"},
            "database": {"password": "synthetic-production-password", "read_only": False},
        },
    }
    monkeypatch.setattr(SettingsLoader, "_read_yaml", staticmethod(lambda path: deepcopy(files[path.name])))
    monkeypatch.setattr(Path, "is_file", lambda path: path.name == "production.local.yaml")
    return files


def test_production_local_overlay_is_isolated_and_redacted(synthetic_config: dict[str, dict[str, Any]]) -> None:
    """production 加载本机覆盖且强制只读，test 不读本机生产凭据，repr 不泄露密码。"""
    production = SettingsLoader.load("production", Path("/synthetic-project"))
    test = SettingsLoader.load("test", Path("/synthetic-project"))
    assert production.factor_data.auth_token == "synthetic-production-token"
    assert production.database.read_only is True
    assert test.factor_data.auth_token == "synthetic-test-token"
    assert test.database.password is None
    assert test.database.read_only is False
    assert "synthetic-production-token" not in repr(production)
    assert "synthetic-production-password" not in repr(production)


def test_runtime_overrides_win_without_disabling_production_readonly(
    synthetic_config: dict[str, dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """环境变量优先于本机文件，但生产只读不能通过变量关闭。"""
    monkeypatch.setenv("AUTOMATION_FACTOR_DATA_MCP_TOKEN", "synthetic-runtime-token")
    monkeypatch.setenv("AUTOMATION_DB_READ_ONLY", "false")
    settings = SettingsLoader.load("production", Path("/synthetic-project"))
    assert settings.factor_data.auth_token == "synthetic-runtime-token"
    assert settings.database.read_only is True


def test_host_override_variables_never_cross_environments(
    synthetic_config: dict[str, dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """test allowlist 变量不影响production，production allowlist变量也不影响test。"""
    monkeypatch.setenv("AUTOMATION_TEST_ALLOWED_HOSTS", "test-factor-frontend.questvector.ai")
    monkeypatch.setenv("AUTOMATION_TEST_ALLOWED_DATABASE_HOSTS", "43.167.190.122")
    monkeypatch.setenv("AUTOMATION_PRODUCTION_ALLOWED_HOSTS", "factor-frontend.questvector.ai")
    monkeypatch.setenv("AUTOMATION_PRODUCTION_ALLOWED_DATABASE_HOSTS", "db.example.org")
    production = SettingsLoader.load("production", Path("/synthetic-project"))
    test = SettingsLoader.load("test", Path("/synthetic-project"))
    assert production.environment_safety.allowed_hosts == ("factor-frontend.questvector.ai",)
    assert production.environment_safety.allowed_database_hosts == ("db.example.org",)
    assert test.environment_safety.allowed_hosts == ("test-factor-frontend.questvector.ai",)
    assert test.environment_safety.allowed_database_hosts == ("43.167.190.122",)


def test_missing_optional_local_file_uses_public_production_config(
    synthetic_config: dict[str, dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有本机覆盖仍可加载production骨架，不静默继承test Token或账号。"""
    monkeypatch.setattr(Path, "is_file", lambda path: False)
    production = SettingsLoader.load("production", Path("/synthetic-project"))
    assert production.factor_data.auth_token is None
    assert production.authentication.privileged.email is None
    assert production.database.read_only is True


def test_compression_is_optional_and_runtime_override_keeps_readonly(
    synthetic_config: dict[str, dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """压缩默认关闭，生产YAML可启用且环境变量可关闭，不影响只读或mysql驱动。"""
    test = SettingsLoader.load("test", Path("/synthetic-project"))
    assert test.database.compress is False
    synthetic_config["production.yaml"]["database"] = {"driver": "mysql", "compress": True}
    production = SettingsLoader.load("production", Path("/synthetic-project"))
    assert production.database.compress is True
    assert production.database.driver == "mysql"
    monkeypatch.setenv("AUTOMATION_DB_COMPRESS", "false")
    overridden = SettingsLoader.load("production", Path("/synthetic-project"))
    assert overridden.database.compress is False
    assert overridden.database.read_only is True
    assert overridden.database.driver == "mysql"


def test_compression_rejects_nonboolean_override(
    synthetic_config: dict[str, dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """拼错布尔配置应在建立连接前失败，不能悄悄切换数据库客户端。"""
    monkeypatch.setenv("AUTOMATION_DB_COMPRESS", "sometimes")
    with pytest.raises(ValueError, match="boolean-like"):
        SettingsLoader.load("production", Path("/synthetic-project"))
