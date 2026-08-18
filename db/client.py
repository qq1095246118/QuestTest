"""数据库连接、参数化查询和事务管理。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from config.settings import DatabaseSettings


QueryParameters = Sequence[Any] | Mapping[str, Any]


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

    def __init__(self, connection: DatabaseConnection) -> None:
        """初始化事务操作对象。

        参数 ``connection`` 是当前事务独占的 DB-API 连接。
        不返回值；对象仅应在 ``DatabaseClient.transaction`` 的上下文中使用。
        """

        self._connection = connection

    def execute(self, query: str, parameters: QueryParameters | None = None) -> ExecutionResult:
        """在当前事务中执行参数化写 SQL。

        参数 ``query`` 是 SQL 模板，``parameters`` 是绑定参数。
        返回受影响行数和最后插入 ID；SQL 执行异常交由事务上下文回滚并继续抛出。
        """

        cursor = self._connection.cursor()
        try:
            self._execute_cursor(cursor, query, parameters)
            return ExecutionResult(rowcount=cursor.rowcount, lastrowid=cursor.lastrowid)
        finally:
            cursor.close()

    def fetch_one(self, query: str, parameters: QueryParameters | None = None) -> dict[str, Any] | None:
        """在当前事务中查询至多一条记录。

        参数 ``query`` 是 SQL 模板，``parameters`` 是绑定参数。
        返回字段名到值的字典；查询无结果时返回 ``None``。
        """

        cursor = self._connection.cursor()
        try:
            self._execute_cursor(cursor, query, parameters)
            row = cursor.fetchone()
            return self._to_mapping(cursor, row) if row is not None else None
        finally:
            cursor.close()

    def fetch_all(self, query: str, parameters: QueryParameters | None = None) -> list[dict[str, Any]]:
        """在当前事务中查询多条记录。

        参数 ``query`` 是 SQL 模板，``parameters`` 是绑定参数。
        返回按字段名转换后的记录列表；查询无结果时返回空列表。
        """

        cursor = self._connection.cursor()
        try:
            self._execute_cursor(cursor, query, parameters)
            return [self._to_mapping(cursor, row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    @staticmethod
    def _execute_cursor(cursor: DatabaseCursor, query: str, parameters: QueryParameters | None) -> None:
        """统一执行带或不带参数的 SQL。

        参数 ``cursor`` 是已打开 Cursor，``query`` 是 SQL 模板，``parameters`` 是可选绑定参数。
        不返回值；避免将用户输入拼接进 SQL，驱动异常直接向上传递。
        """

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

    def __init__(self, connection_factory: Callable[[], DatabaseConnection]) -> None:
        """初始化数据库客户端。

        参数 ``connection_factory`` 每次调用时返回一个独立 DB-API 连接。
        不返回值；客户端负责在每个操作结束后关闭连接。
        """

        self._connection_factory = connection_factory

    @classmethod
    def from_settings(cls, settings: DatabaseSettings) -> DatabaseClient:
        """根据类型化配置创建 SQLite 或 MySQL 数据库客户端。

        参数 ``settings`` 包含驱动和连接字段；支持 ``sqlite`` 与 ``mysql``。
        返回 ``DatabaseClient``；不支持的驱动或缺少 MySQL 必填字段时抛出 ``ValueError``。
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
                return connection

            return cls(connection_factory)

        if settings.driver == "mysql":
            if not all((settings.host, settings.port, settings.name, settings.username)):
                raise ValueError("MySQL host, port, name and username must be configured")

            def connection_factory() -> DatabaseConnection:
                """创建一个 DictCursor 模式的 MySQL 连接。

                不接收参数。
                返回新的 PyMySQL 连接；连接失败时抛出驱动异常。
                """

                import pymysql

                return pymysql.connect(
                    host=settings.host,
                    port=settings.port,
                    user=settings.username,
                    password=settings.password,
                    database=settings.name,
                    charset="utf8mb4",
                    cursorclass=pymysql.cursors.DictCursor,
                    autocommit=False,
                )

            return cls(connection_factory)

        raise ValueError(f"Unsupported database driver: {settings.driver}")

    @contextmanager
    def transaction(self) -> Iterator[DatabaseTransaction]:
        """开启一个自动提交或回滚并最终关闭的数据库事务。

        不接收参数。
        生成 ``DatabaseTransaction`` 供上下文内执行读写；正常退出时提交，任意异常时回滚并继续抛出。
        """

        connection = self._connection_factory()
        try:
            yield DatabaseTransaction(connection)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def execute(self, query: str, parameters: QueryParameters | None = None) -> ExecutionResult:
        """在独立事务中执行一条参数化写 SQL。

        参数 ``query`` 是 SQL 模板，``parameters`` 是绑定参数。
        返回 ``ExecutionResult``；执行失败时自动回滚并抛出驱动异常。
        """

        with self.transaction() as transaction:
            return transaction.execute(query, parameters)

    def fetch_one(self, query: str, parameters: QueryParameters | None = None) -> dict[str, Any] | None:
        """使用短生命周期连接查询至多一条记录。

        参数 ``query`` 是 SQL 模板，``parameters`` 是绑定参数。
        返回字段字典或 ``None``；查询结束后关闭连接。
        """

        connection = self._connection_factory()
        try:
            return DatabaseTransaction(connection).fetch_one(query, parameters)
        finally:
            connection.close()

    def fetch_all(self, query: str, parameters: QueryParameters | None = None) -> list[dict[str, Any]]:
        """使用短生命周期连接查询多条记录。

        参数 ``query`` 是 SQL 模板，``parameters`` 是绑定参数。
        返回字段字典列表；查询结束后关闭连接。
        """

        connection = self._connection_factory()
        try:
            return DatabaseTransaction(connection).fetch_all(query, parameters)
        finally:
            connection.close()
