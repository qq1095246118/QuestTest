"""数据库连接、参数化查询和事务管理。"""

from __future__ import annotations

import re
import sqlite3
from math import ceil
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from config.settings import DatabaseSettings


QueryParameters = Sequence[Any] | Mapping[str, Any]


_SQL_PARTS = re.compile(
    r"'(?:''|\\.|[^'\\])*'|\"(?:\"\"|\\.|[^\"\\])*\"|`(?:``|[^`])*`"
    r"|/\*.*?\*/|--(?=\s|$)[^\n]*|\#[^\n]*|[A-Za-z_][A-Za-z_0-9]*|;|[^\s]",
    re.DOTALL,
)
_READ_ONLY_CONTROLS = {
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ",
    "SET TRANSACTION READ ONLY",
    "START TRANSACTION WITH CONSISTENT SNAPSHOT",
    "START TRANSACTION WITH CONSISTENT SNAPSHOT , READ ONLY",
    "START TRANSACTION READ ONLY",
    "ROLLBACK",
}


def _require_read_only_sql(query: str) -> None:
    """拒绝只读连接上的写语句、锁读、多语句和可执行注释，不暴露 SQL 文本。"""

    tokens: list[str] = []
    for part in _SQL_PARTS.findall(query):
        if part.startswith(("/*!", "/*M!", "/*m!")):
            raise PermissionError("Read-only database connection rejects executable SQL comments")
        if part.startswith(("/*", "--", "#")):
            continue
        tokens.append("<quoted>" if part[0] in "'\"`" else part.upper())
    if tokens and tokens[-1] == ";":
        tokens.pop()
    if not tokens or ";" in tokens:
        raise PermissionError("Read-only database connection requires one read statement")
    normalized = " ".join(tokens)
    if normalized in _READ_ONLY_CONTROLS:
        return
    if tokens[0] not in {"SELECT", "WITH", "SHOW", "DESCRIBE", "DESC"}:
        raise PermissionError("Read-only database connection rejects non-query SQL")
    prohibited = {
        "INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP",
        "TRUNCATE", "GRANT", "REVOKE", "CALL", "INTO", "OUTFILE", "DUMPFILE",
        "GET_LOCK", "RELEASE_LOCK", "RELEASE_ALL_LOCKS",
    }
    if prohibited.intersection(tokens) or any(
        phrase in normalized for phrase in ("FOR SHARE", "LOCK IN SHARE MODE", ": =")
    ):
        raise PermissionError("Read-only database connection rejects modifying or locking SQL")


class DatabaseCursor(Protocol):
    """描述 DB-API Cursor 的最小能力。"""

    description: Sequence[Sequence[Any]] | None
    rowcount: int
    lastrowid: int | None

    def execute(self, query: str, parameters: QueryParameters | None = None) -> Any:
        """执行参数化 SQL。

        参数 ``query`` 是 SQL 模板，``parameters`` 是绑定参数。
        返回由具体 DB 驱动决定；SQL 或连接错误时抛出驱动异常。
        """

    def fetchone(self) -> Any:
        """读取一条查询结果。

        不接收参数。
        返回一条结果或 ``None``；由具体驱动决定原始行类型。
        """

    def fetchall(self) -> Sequence[Any]:
        """读取全部查询结果。

        不接收参数。
        返回原始行组成的序列；由具体驱动决定行类型。
        """

    def close(self) -> None:
        """关闭 Cursor。

        不接收参数，也不返回值。
        """


class DatabaseConnection(Protocol):
    """描述 DB-API Connection 的最小能力。"""

    def cursor(self) -> DatabaseCursor:
        """创建数据库 Cursor。

        不接收参数。
        返回可执行参数化 SQL 的 Cursor。
        """

    def commit(self) -> None:
        """提交当前事务。

        不接收参数，也不返回值；提交失败时抛出驱动异常。
        """

    def rollback(self) -> None:
        """回滚当前事务。

        不接收参数，也不返回值；回滚失败时抛出驱动异常。
        """

    def close(self) -> None:
        """关闭数据库连接。

        不接收参数，也不返回值。
        """


def _cleanup_during_error(cleanup: Callable[[], None]) -> None:
    """已有异常正在传播时尽力清理；不让次级异常覆盖它，也不输出清理错误文本。"""

    try:
        cleanup()
    except BaseException:
        pass


@contextmanager
def _cleanup_after(cleanup: Callable[[], None]) -> Iterator[None]:
    """退出时调用清理函数；保留正文原异常，正文成功时正常抛出清理失败。"""

    try:
        yield
    except BaseException:
        _cleanup_during_error(cleanup)
        raise
    else:
        cleanup()


class _CompressedMySQLConnection:
    """提供 Connector/Python 字典游标，维持现有 DB-API 连接接口。"""

    def __init__(self, connection: Any) -> None:
        """保存已连接对象；无返回值，不执行查询。"""
        self._connection = connection

    def cursor(self) -> DatabaseCursor:
        """返回已缓冲字典游标；创建失败时抛出驱动异常。"""
        return self._connection.cursor(dictionary=True, buffered=True)

    def commit(self) -> None:
        """提交当前事务；无输入/返回值，失败时抛出驱动异常。"""
        self._connection.commit()

    def rollback(self) -> None:
        """回滚当前事务；无输入/返回值，失败时抛出驱动异常。"""
        self._connection.rollback()

    def close(self) -> None:
        """关闭当前连接；无输入/返回值，失败时抛出驱动异常。"""
        self._connection.close()


@dataclass(frozen=True)
class ExecutionResult:
    """描述一条写 SQL 的执行结果。

    参数 ``rowcount`` 是受影响行数，``lastrowid`` 是驱动提供的最后插入 ID。
    返回值由 ``DatabaseTransaction.execute`` 和 ``DatabaseClient.execute`` 产生。
    """

    rowcount: int
    lastrowid: int | None


class DatabaseTransaction:
    """封装单个已打开连接上的事务内查询和写操作。"""

    def __init__(self, connection: DatabaseConnection, *, read_only: bool = False) -> None:
        """初始化事务操作对象。

        参数 ``connection`` 是当前事务独占的 DB-API 连接，``read_only`` 禁止写 SQL。
        不返回值；对象仅应在 ``DatabaseClient.transaction`` 的上下文中使用。
        """

        self._connection = connection
        self._read_only = read_only

    def execute(self, query: str, parameters: QueryParameters | None = None) -> ExecutionResult:
        """在当前事务中执行参数化写 SQL。

        参数 ``query`` 是 SQL 模板，``parameters`` 是绑定参数。
        返回受影响行数和最后插入 ID；只读模式拒绝写入并抛出 ``PermissionError``，驱动异常继续抛出。
        """

        cursor = self._connection.cursor()
        with _cleanup_after(cursor.close):
            self._execute_cursor(cursor, query, parameters)
            return ExecutionResult(rowcount=cursor.rowcount, lastrowid=cursor.lastrowid)

    def fetch_one(self, query: str, parameters: QueryParameters | None = None) -> dict[str, Any] | None:
        """在当前事务中查询至多一条记录。

        参数 ``query`` 是 SQL 模板，``parameters`` 是绑定参数。
        返回字段名到值的字典；无结果时返回 ``None``；只读模式拒绝非查询 SQL 并抛出 ``PermissionError``。
        """

        cursor = self._connection.cursor()
        with _cleanup_after(cursor.close):
            self._execute_cursor(cursor, query, parameters)
            row = cursor.fetchone()
            return self._to_mapping(cursor, row) if row is not None else None

    def fetch_all(self, query: str, parameters: QueryParameters | None = None) -> list[dict[str, Any]]:
        """在当前事务中查询多条记录。

        参数 ``query`` 是 SQL 模板，``parameters`` 是绑定参数。
        返回按字段名转换后的记录列表；无结果时返回空列表；只读模式拒绝非查询 SQL 并抛出 ``PermissionError``。
        """

        cursor = self._connection.cursor()
        with _cleanup_after(cursor.close):
            self._execute_cursor(cursor, query, parameters)
            return [self._to_mapping(cursor, row) for row in cursor.fetchall()]

    def _execute_cursor(self, cursor: DatabaseCursor, query: str, parameters: QueryParameters | None) -> None:
        """统一执行带或不带参数的 SQL。

        参数 ``cursor`` 是已打开 Cursor，``query`` 是 SQL 模板，``parameters`` 是可选绑定参数。
        不返回值；避免将用户输入拼接进 SQL，驱动异常直接向上传递。
        """

        if self._read_only:
            _require_read_only_sql(query)
        if parameters is None:
            cursor.execute(query)
        else:
            cursor.execute(query, parameters)

    @staticmethod
    def _to_mapping(cursor: DatabaseCursor, row: Any) -> dict[str, Any]:
        """把不同 DB 驱动返回的行对象标准化为字典。

        参数 ``cursor`` 提供列定义，``row`` 是驱动返回的一条结果。
        返回字段名到值的字典；无法根据 Cursor 描述转换时抛出 ``ValueError``。
        """

        if isinstance(row, Mapping):
            return dict(row)
        if hasattr(row, "keys"):
            keys = row.keys()
            return {key: row[key] for key in keys}
        if cursor.description is None:
            raise ValueError("Query result does not expose column descriptions")
        column_names = [str(column[0]) for column in cursor.description]
        return dict(zip(column_names, row, strict=True))


class DatabaseClient:
    """创建短生命周期连接并提供查询、事务和安全写入能力。"""

    def __init__(
        self, connection_factory: Callable[[], DatabaseConnection], *, read_only: bool = False
    ) -> None:
        """初始化数据库客户端。

        参数 ``connection_factory`` 返回独立 DB-API 连接，``read_only`` 对所有执行入口强制只读。
        不返回值；客户端负责在每个操作结束后关闭连接。
        """

        self._connection_factory = connection_factory
        self._read_only = read_only

    @classmethod
    def from_settings(cls, settings: DatabaseSettings) -> DatabaseClient:
        """根据类型化配置创建 SQLite 或 MySQL 数据库客户端。

        参数 ``settings`` 包含驱动、连接字段、超时、压缩及只读开关；支持 ``sqlite`` 与 ``mysql``。
        返回 ``DatabaseClient``；不支持的驱动或缺少 MySQL 必填字段时抛出 ``ValueError``。
        只读连接须先成功启用会话只读；启用失败时关闭连接并抛出驱动异常。
        """

        if settings.driver == "sqlite":
            dsn = settings.dsn or ":memory:"

            def connection_factory() -> sqlite3.Connection:
                """创建一个带行对象支持的 SQLite 连接。

                不接收参数。
                返回新的 SQLite 连接，用于一次数据库操作或事务。
                """

                connection = sqlite3.connect(dsn)
                connection.row_factory = sqlite3.Row
                if settings.read_only:
                    connection.execute("PRAGMA query_only = ON")
                return connection

            return cls(connection_factory, read_only=settings.read_only)

        if settings.driver == "mysql":
            if not all((settings.host, settings.port, settings.name, settings.username)):
                raise ValueError("MySQL host, port, name and username must be configured")

            def connection_factory() -> DatabaseConnection:
                """创建返回字典的 MySQL 连接，可选 Connector/Python 协议压缩。

                不接收参数。
                返回独立连接；压缩连接失败仅抛脱敏 RuntimeError，默认分支保持驱动异常。
                """

                if settings.compress:
                    try:
                        import mysql.connector

                        connection = _CompressedMySQLConnection(mysql.connector.connect(
                            host=settings.host, port=settings.port, user=settings.username,
                            password=settings.password, database=settings.name,
                            charset="utf8mb4", autocommit=False, compress=True,
                            use_pure=False, allow_local_infile=False,
                            connection_timeout=ceil(settings.connect_timeout_seconds),
                            read_timeout=ceil(settings.read_timeout_seconds),
                            write_timeout=ceil(settings.write_timeout_seconds),
                        ))
                    except Exception as error:
                        raise RuntimeError(
                            f"MySQL compressed connection failed: {type(error).__name__}"
                        ) from None
                else:
                    import pymysql

                    connection = pymysql.connect(
                        host=settings.host,
                        port=settings.port,
                        user=settings.username,
                        password=settings.password,
                        database=settings.name,
                        charset="utf8mb4",
                        cursorclass=pymysql.cursors.DictCursor,
                        autocommit=False,
                        connect_timeout=settings.connect_timeout_seconds,
                        read_timeout=settings.read_timeout_seconds,
                        write_timeout=settings.write_timeout_seconds,
                    )
                if settings.read_only:
                    try:
                        cursor = connection.cursor()
                        with _cleanup_after(cursor.close):
                            cursor.execute("SET SESSION TRANSACTION READ ONLY")
                    except BaseException:
                        _cleanup_during_error(connection.close)
                        raise
                return connection

            return cls(connection_factory, read_only=settings.read_only)

        raise ValueError(f"Unsupported database driver: {settings.driver}")

    @contextmanager
    def transaction(self) -> Iterator[DatabaseTransaction]:
        """开启一个自动提交或回滚并最终关闭的数据库事务。

        不接收参数。
        生成 ``DatabaseTransaction``；只读模式正常退出也回滚，其他模式提交，异常时回滚并继续抛出。
        已有正文或提交异常时，回滚和关闭的次级失败不会覆盖原异常；成功后的清理失败仍抛出。
        """

        connection = self._connection_factory()
        with _cleanup_after(connection.close):
            try:
                yield DatabaseTransaction(connection, read_only=self._read_only)
                if self._read_only:
                    connection.rollback()
                else:
                    connection.commit()
            except BaseException:
                _cleanup_during_error(connection.rollback)
                raise

    def execute(self, query: str, parameters: QueryParameters | None = None) -> ExecutionResult:
        """在独立事务中执行一条参数化写 SQL。

        参数 ``query`` 是 SQL 模板，``parameters`` 是绑定参数。
        返回 ``ExecutionResult``；只读模式拒绝写入并抛出 ``PermissionError``，驱动异常自动回滚后抛出。
        """

        with self.transaction() as transaction:
            return transaction.execute(query, parameters)

    def fetch_one(self, query: str, parameters: QueryParameters | None = None) -> dict[str, Any] | None:
        """使用短生命周期连接查询至多一条记录。

        参数 ``query`` 是 SQL 模板，``parameters`` 是绑定参数。
        返回字段字典或 ``None``；查询结束后关闭连接；只读模式拒绝非查询 SQL 并抛出 ``PermissionError``。
        """

        connection = self._connection_factory()
        with _cleanup_after(connection.close):
            return DatabaseTransaction(connection, read_only=self._read_only).fetch_one(query, parameters)

    def fetch_all(self, query: str, parameters: QueryParameters | None = None) -> list[dict[str, Any]]:
        """使用短生命周期连接查询多条记录。

        参数 ``query`` 是 SQL 模板，``parameters`` 是绑定参数。
        返回字段字典列表；查询结束后关闭连接；只读模式拒绝非查询 SQL 并抛出 ``PermissionError``。
        """

        connection = self._connection_factory()
        with _cleanup_after(connection.close):
            return DatabaseTransaction(connection, read_only=self._read_only).fetch_all(query, parameters)
