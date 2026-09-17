"""数据库连接配置和 MySQL 超时透传的离线单元测试。"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime
from decimal import Decimal
import sys
from types import ModuleType
from typing import Any
from unittest.mock import Mock
import traceback

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


@pytest.mark.parametrize("method", ["execute", "fetch_one", "fetch_all"])
@pytest.mark.parametrize(
    "query",
    [
        "UPDATE sample SET value = 1",
        "WITH ids AS (SELECT 1) DELETE FROM sample WHERE value IN (SELECT * FROM ids)",
        "SELECT value INTO OUTFILE '/tmp/export' FROM sample",
        "SELECT value FROM sample FOR UPDATE",
        "SELECT value FROM sample FOR SHARE",
        "SELECT value FROM sample LOCK IN SHARE MODE",
        "SELECT 1; DELETE FROM sample",
        "SELECT 1 /*!50000 INTO OUTFILE '/tmp/export' */",
        "SET SESSION TRANSACTION READ WRITE",
        "START TRANSACTION READ WRITE",
        "SELECT GET_LOCK('sample', 1)",
    ],
)
def test_read_only_database_rejects_writes_through_every_query_method(
    method: str, query: str,
) -> None:
    """任何公共执行入口都在 Cursor 执行前拒绝写入、锁读及绕过只读控制的 SQL。"""

    connection = StubConnection()
    client = DatabaseClient(lambda: connection, read_only=True)

    with pytest.raises(PermissionError, match="Read-only database"):
        getattr(client, method)(query)

    assert connection.stub_cursor.calls == []
    assert connection.closed is True
    assert connection.committed is False


def test_read_only_transaction_supports_factor4_consistent_snapshot_queries() -> None:
    """4.0 的只读一致性快照控制、CTE、引号字面量和绑定参数均可执行，退出只回滚。"""

    connection = StubConnection()
    client = DatabaseClient(lambda: connection, read_only=True)
    with client.transaction() as transaction:
        transaction.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        transaction.execute("SET TRANSACTION READ ONLY")
        transaction.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        result = transaction.fetch_all(
            "WITH source AS (SELECT updated_at, 'DELETE; UPDATE' AS `update` FROM sample) "
            "SELECT updated_at FROM source WHERE id = %s; -- read query",
            (42,),
        )
        transaction.execute("ROLLBACK")

    assert result == [{"value": 1}]
    assert len(connection.stub_cursor.calls) == 5
    assert connection.stub_cursor.calls[3][1] == (42,)
    assert connection.rolled_back is True
    assert connection.committed is False
    assert connection.closed is True


def test_mysql_read_only_settings_protect_every_new_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """生产配置创建的每个 MySQL 连接必须先启用数据库会话级只读，再允许查询。"""

    connections: list[StubConnection] = []

    def fake_connect(**kwargs: Any) -> StubConnection:
        """返回新的离线连接并记录它，不接触真实数据库。"""

        connection = StubConnection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(pymysql, "connect", fake_connect)
    settings = DatabaseSettings(
        driver="mysql", host="db.example.test", port=3306, name="sample",
        username="reader", password=None, dsn=None, read_only=True,
    )
    client = DatabaseClient.from_settings(settings)

    client.fetch_one("SELECT 1")
    client.fetch_all("SHOW TABLES")

    assert len(connections) == 2
    for connection in connections:
        assert connection.stub_cursor.calls[0] == ("SET SESSION TRANSACTION READ ONLY", None)
        assert connection.closed is True
    with pytest.raises(PermissionError):
        client.execute("DELETE FROM sample")


def test_test_database_retains_write_commit_and_rollback(tmp_path: Path) -> None:
    """默认测试配置仍可写入和提交；异常事务回滚，不受生产只读开关影响。"""

    settings = DatabaseSettings(
        driver="sqlite", host="", port=0, name="", username="", password=None,
        dsn=str(tmp_path / "write-test.sqlite"),
    )
    client = DatabaseClient.from_settings(settings)
    client.execute("CREATE TABLE sample (value INTEGER)")
    client.execute("INSERT INTO sample VALUES (?)", (1,))
    with pytest.raises(RuntimeError):
        with client.transaction() as transaction:
            transaction.execute("INSERT INTO sample VALUES (?)", (2,))
            raise RuntimeError("rollback sample")

    assert client.fetch_all("SELECT value FROM sample") == [{"value": 1}]


def test_compressed_mysql_preserves_types_dictionary_results_and_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """压缩分支保留驱动原始类型与完整 JSON，所有游标仍为字典并启用会话只读。"""
    value = {"number": Decimal("1.230000000001"), "at": datetime(2026, 9, 10, 1, 2, 3),
             "payload": '{"missing":null,"unicode":"因子"}', "flag": 1, "empty": None}
    connection = StubConnection()
    cursor_options: list[dict[str, Any]] = []
    connect_options: dict[str, Any] = {}

    def cursor(**kwargs: Any) -> StubCursor:
        """记录游标选择并返回固定类型结果；无外部访问。"""
        cursor_options.append(kwargs)
        return connection.stub_cursor

    def connect(**kwargs: Any) -> StubConnection:
        """记录压缩连接选项并返回替身；不创建实际连接。"""
        connect_options.update(kwargs)
        return connection

    monkeypatch.setattr(connection, "cursor", cursor)
    monkeypatch.setattr(connection.stub_cursor, "fetchall", lambda: [value])
    package, connector = ModuleType("mysql"), ModuleType("mysql.connector")
    package.connector = connector  # type: ignore[attr-defined]
    connector.connect = connect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mysql", package)
    monkeypatch.setitem(sys.modules, "mysql.connector", connector)
    settings = DatabaseSettings(
        driver="mysql", host="db.example.test", port=3306, name="sample",
        username="reader", password="offline-secret", dsn=None, read_only=True,
        compress=True, connect_timeout_seconds=2.5, read_timeout_seconds=11.2,
    )
    client = DatabaseClient.from_settings(settings)
    assert client.fetch_all("SELECT number, at, payload, flag, empty FROM sample") == [value]
    assert all(options == {"dictionary": True, "buffered": True} for options in cursor_options)
    assert connect_options["compress"] is True
    assert connect_options["use_pure"] is False
    assert connect_options["allow_local_infile"] is False
    assert connect_options["connection_timeout"] == 3
    assert connect_options["read_timeout"] == 12
    assert connection.stub_cursor.calls[0] == ("SET SESSION TRANSACTION READ ONLY", None)
    assert connection.closed is True


def test_compressed_mysql_connection_error_does_not_expose_connection_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """新驱动连接错误仅保留异常类型，不输出凭据或原始数据库报错。"""
    def connect(**kwargs: Any) -> None:
        """模拟带密码的驱动错误，不访问外部连接。"""
        raise ValueError("sensitive-host sensitive-password")

    package, connector = ModuleType("mysql"), ModuleType("mysql.connector")
    package.connector = connector  # type: ignore[attr-defined]
    connector.connect = connect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mysql", package)
    monkeypatch.setitem(sys.modules, "mysql.connector", connector)
    settings = DatabaseSettings(
        driver="mysql", host="db.example.test", port=3306, name="sample",
        username="reader", password=None, dsn=None, read_only=True, compress=True,
    )
    with pytest.raises(RuntimeError) as error:
        DatabaseClient.from_settings(settings).fetch_one("SELECT 1")
    assert str(error.value) == "MySQL compressed connection failed: ValueError"


@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("original", [ValueError("business failure"), KeyboardInterrupt("cancelled")])
def test_transaction_body_error_survives_rollback_and_connection_close_failures(
    monkeypatch: pytest.MonkeyPatch, read_only: bool, original: BaseException,
) -> None:
    """事务正文失败或取消时仍尝试回滚和关闭，两个清理错误都不能替换原异常对象。"""
    connection = StubConnection()
    rollback = Mock(side_effect=RuntimeError("rollback failed"))
    close = Mock(side_effect=RuntimeError("close failed"))
    monkeypatch.setattr(connection, "rollback", rollback)
    monkeypatch.setattr(connection, "close", close)
    client = DatabaseClient(lambda: connection, read_only=read_only)

    with pytest.raises(type(original)) as caught:
        with client.transaction():
            raise original

    assert caught.value is original
    rollback.assert_called_once_with()
    close.assert_called_once_with()
    assert connection.committed is False


@pytest.mark.parametrize("method,failed_cursor_method", [
    ("execute", "execute"), ("fetch_one", "execute"), ("fetch_all", "execute"),
    ("fetch_one", "fetchone"), ("fetch_all", "fetchall"),
])
def test_query_error_survives_cursor_and_connection_cleanup_failures(
    monkeypatch: pytest.MonkeyPatch, method: str, failed_cursor_method: str,
) -> None:
    """执行或读取失败仍暴露原驱动异常，不能被游标关闭或连接清理错误掩盖。"""
    connection = StubConnection()
    original = pymysql.err.OperationalError(2013, "read failed")
    operation = Mock(side_effect=original)
    cursor_close = Mock(side_effect=RuntimeError("cursor close failed"))
    close = Mock(side_effect=RuntimeError("connection close failed"))
    rollback = Mock(side_effect=RuntimeError("rollback failed"))
    monkeypatch.setattr(connection.stub_cursor, failed_cursor_method, operation)
    monkeypatch.setattr(connection.stub_cursor, "close", cursor_close)
    monkeypatch.setattr(connection, "close", close)
    monkeypatch.setattr(connection, "rollback", rollback)

    with pytest.raises(pymysql.err.OperationalError) as caught:
        getattr(DatabaseClient(lambda: connection, read_only=True), method)("SELECT value FROM sample")

    assert caught.value is original
    cursor_close.assert_called_once_with()
    close.assert_called_once_with()
    assert rollback.call_count == (1 if method == "execute" else 0)
    assert connection.committed is False


@pytest.mark.parametrize("read_only", [False, True])
def test_transaction_finalization_error_survives_later_cleanup_failures(
    monkeypatch: pytest.MonkeyPatch, read_only: bool,
) -> None:
    """正文成功后的提交或只读回滚失败仍是主异常，后续回滚和关闭失败不能覆盖它。"""
    connection = StubConnection()
    original = RuntimeError("transaction finalization failed")
    rollback = Mock(side_effect=[original, ValueError("second rollback failed")] if read_only
                    else ValueError("rollback failed"))
    commit = Mock(side_effect=original)
    close = Mock(side_effect=ValueError("close failed"))
    monkeypatch.setattr(connection, "rollback", rollback)
    monkeypatch.setattr(connection, "commit", commit)
    monkeypatch.setattr(connection, "close", close)

    with pytest.raises(RuntimeError) as caught:
        with DatabaseClient(lambda: connection, read_only=read_only).transaction():
            pass

    assert caught.value is original
    assert rollback.call_count == (2 if read_only else 1)
    assert commit.call_count == (0 if read_only else 1)
    close.assert_called_once_with()


@pytest.mark.parametrize("method", ["execute", "fetch_one", "fetch_all"])
@pytest.mark.parametrize("stage", ["cursor", "connection"])
def test_successful_query_does_not_suppress_its_only_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch, method: str, stage: str,
) -> None:
    """没有先前错误时，游标或连接关闭失败仍使操作失败，不被清理保护静默吞掉。"""
    connection = StubConnection()
    original = RuntimeError("cleanup failed after successful query")
    target = connection.stub_cursor if stage == "cursor" else connection
    monkeypatch.setattr(target, "close", Mock(side_effect=original))

    with pytest.raises(RuntimeError) as caught:
        getattr(DatabaseClient(lambda: connection), method)("SELECT value FROM sample")

    assert caught.value is original
    if method == "execute":
        assert connection.committed is (stage == "connection")
        assert connection.rolled_back is (stage == "cursor")


@pytest.mark.parametrize("method", ["execute", "fetch_one", "fetch_all"])
def test_read_only_rejection_is_preserved_when_all_cleanup_steps_fail(
    monkeypatch: pytest.MonkeyPatch, method: str,
) -> None:
    """即使清理全部失败，只读保护仍在 SQL 执行前拒绝写入并保留 PermissionError。"""
    connection = StubConnection()
    for target, name in ((connection.stub_cursor, "close"), (connection, "rollback"), (connection, "close")):
        monkeypatch.setattr(target, name, Mock(side_effect=RuntimeError("cleanup failed")))

    with pytest.raises(PermissionError, match="Read-only database"):
        getattr(DatabaseClient(lambda: connection, read_only=True), method)("DELETE FROM sample")

    assert connection.stub_cursor.calls == []
    assert connection.committed is False


def test_read_only_session_initialization_error_is_preserved_and_connection_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """会话只读设置失败时保留原错误并尝试清理，不能继续执行调用方的查询。"""
    connection = StubConnection()
    original = pymysql.err.OperationalError(2013, "session initialization failed")
    execute = Mock(side_effect=original)
    cursor_close = Mock(side_effect=RuntimeError("cursor close failed"))
    close = Mock(side_effect=RuntimeError("connection close failed"))
    monkeypatch.setattr(connection.stub_cursor, "execute", execute)
    monkeypatch.setattr(connection.stub_cursor, "close", cursor_close)
    monkeypatch.setattr(connection, "close", close)
    monkeypatch.setattr(pymysql, "connect", Mock(return_value=connection))
    settings = DatabaseSettings(
        driver="mysql", host="db.example.test", port=3306, name="sample",
        username="reader", password=None, dsn=None, read_only=True,
    )

    with pytest.raises(pymysql.err.OperationalError) as caught:
        DatabaseClient.from_settings(settings).fetch_one("SELECT 1")

    assert caught.value is original
    execute.assert_called_once_with("SET SESSION TRANSACTION READ ONLY")
    cursor_close.assert_called_once_with()
    close.assert_called_once_with()


def test_secondary_cleanup_error_text_is_not_added_to_original_exception_or_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """清理错误中的敏感文本不加入日志、原异常说明或可渲染异常链。"""
    connection = StubConnection()
    original = ValueError("original business error")
    secret_marker = "offline-cleanup-secret-marker"
    monkeypatch.setattr(connection, "rollback", Mock(side_effect=RuntimeError(secret_marker)))
    monkeypatch.setattr(connection, "close", Mock(side_effect=RuntimeError(secret_marker)))

    with pytest.raises(ValueError) as caught:
        with DatabaseClient(lambda: connection).transaction():
            raise original

    assert caught.value is original
    assert str(caught.value) == "original business error"
    assert secret_marker not in "".join(traceback.format_exception(caught.value))
    assert secret_marker not in caplog.text
