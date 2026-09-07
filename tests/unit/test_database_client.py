"""数据库连接配置和 MySQL 超时透传的离线单元测试。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymysql
import pytest

from config.settings import DatabaseSettings, SettingsLoader
from db.client import DatabaseClient, QueryParameters


pytestmark = pytest.mark.unit


class StubCursor:
    """提供一条固定查询结果并记录 SQL 的 DB-API Cursor 替身。"""

    description = (("value",),)
    rowcount = 0
    lastrowid = None

    def __init__(self) -> None:
        """初始化 SQL 记录和关闭状态。"""

        self.calls: list[tuple[str, QueryParameters | None]] = []
        self.closed = False

    def execute(self, query: str, parameters: QueryParameters | None = None) -> None:
        """记录参数化 SQL；不访问数据库，也不返回结果。"""

        self.calls.append((query, parameters))

    def fetchone(self) -> dict[str, int]:
        """返回固定单行结果。"""

        return {"value": 1}

    def fetchall(self) -> list[dict[str, int]]:
        """返回固定结果列表。"""

        return [{"value": 1}]

    def close(self) -> None:
        """记录 Cursor 已关闭。"""

        self.closed = True


class StubConnection:
    """提供查询和生命周期记录的 DB-API Connection 替身。"""

    def __init__(self) -> None:
        """初始化 Cursor 以及提交、回滚、关闭状态。"""

        self.stub_cursor = StubCursor()
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self) -> StubCursor:
        """返回当前连接唯一的 Cursor 替身。"""

        return self.stub_cursor

    def commit(self) -> None:
        """记录事务已提交。"""

        self.committed = True

    def rollback(self) -> None:
        """记录事务已回滚。"""

        self.rolled_back = True

    def close(self) -> None:
        """记录连接已关闭。"""

        self.closed = True


def test_database_settings_keep_timeout_defaults_for_existing_callers() -> None:
    """旧调用方不传新增字段时仍能构造配置，并获得有限的默认超时。"""

    settings = DatabaseSettings(
        driver="sqlite",
        host="",
        port=0,
        name="",
        username="",
        password=None,
        dsn=":memory:",
    )

    assert settings.connect_timeout_seconds == 10.0
    assert settings.read_timeout_seconds == 60.0
    assert settings.write_timeout_seconds == 60.0


def test_database_timeouts_are_loaded_from_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """三个数据库超时环境变量应覆盖 YAML，并转换为秒数。"""

    monkeypatch.setenv("AUTOMATION_DB_CONNECT_TIMEOUT_SECONDS", "3.5")
    monkeypatch.setenv("AUTOMATION_DB_READ_TIMEOUT_SECONDS", "17")
    monkeypatch.setenv("AUTOMATION_DB_WRITE_TIMEOUT_SECONDS", "19.25")

    settings = SettingsLoader.load(
        environment="test",
        project_root=Path(__file__).resolve().parents[2],
    )

    assert settings.database.connect_timeout_seconds == 3.5
    assert settings.database.read_timeout_seconds == 17.0
    assert settings.database.write_timeout_seconds == 19.25


@pytest.mark.parametrize(
    ("variable", "value", "config_name"),
    [
        ("AUTOMATION_DB_CONNECT_TIMEOUT_SECONDS", "0", "database.connect_timeout_seconds"),
        ("AUTOMATION_DB_READ_TIMEOUT_SECONDS", "-1", "database.read_timeout_seconds"),
        ("AUTOMATION_DB_WRITE_TIMEOUT_SECONDS", "not-a-number", "database.write_timeout_seconds"),
    ],
)
def test_database_timeout_must_be_a_positive_number(
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    value: str,
    config_name: str,
) -> None:
    """零、负数和非数值超时应在建立真实连接前产生明确配置错误。"""

    monkeypatch.setenv(variable, value)

    with pytest.raises(ValueError, match=config_name):
        SettingsLoader.load(
            environment="test",
            project_root=Path(__file__).resolve().parents[2],
        )


def test_mysql_connection_receives_configured_timeouts_without_exposing_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MySQL 连接应接收三类超时，查询保持离线且配置 repr 不包含密码。"""

    connection = StubConnection()
    connect_kwargs: dict[str, Any] = {}

    def fake_connect(**kwargs: Any) -> StubConnection:
        """记录 PyMySQL 连接参数并返回离线连接替身。"""

        connect_kwargs.update(kwargs)
        return connection

    monkeypatch.setattr(pymysql, "connect", fake_connect)
    settings = DatabaseSettings(
        driver="mysql",
        host="db.example.test",
        port=3306,
        name="factor_test",
        username="factor_user",
        password="unit-test-database-password",
        dsn=None,
        connect_timeout_seconds=2.5,
        read_timeout_seconds=31.0,
        write_timeout_seconds=7.5,
    )

    result = DatabaseClient.from_settings(settings).fetch_all(
        "SELECT value FROM sample WHERE id = %s",
        (42,),
    )

    assert result == [{"value": 1}]
    assert connect_kwargs["connect_timeout"] == 2.5
    assert connect_kwargs["read_timeout"] == 31.0
    assert connect_kwargs["write_timeout"] == 7.5
    assert connection.stub_cursor.calls == [("SELECT value FROM sample WHERE id = %s", (42,))]
    assert connection.stub_cursor.closed is True
    assert connection.closed is True
    assert "unit-test-database-password" not in repr(settings)
