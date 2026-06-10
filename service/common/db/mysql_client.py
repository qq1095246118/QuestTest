from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

from config.settings import settings


MUTATING_SQL_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|replace|grant|revoke)\b",
    re.IGNORECASE,
)
INTO_FILE_SQL_RE = re.compile(r"\binto\s+(out|dump)file\b", re.IGNORECASE)
LOCK_FUNCTION_SQL_RE = re.compile(r"\b(get_lock|release_lock)\s*\(", re.IGNORECASE)


class ReadOnlyMySQLClient:
    """只读 MySQL 查询客户端。

    请求参数:
        实例化时接收 MySQL host、port、database、user、password。
    返回值:
        提供只读 SQL 校验、连接复用、fetch_all、fetch_one 和 close 能力的 DB 客户端实例。
    """

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
    ) -> None:
        """初始化只读 MySQL 客户端连接参数。

        请求参数:
            host: MySQL host。
            port: MySQL port。
            database: 数据库名。
            user: 数据库用户名。
            password: 数据库密码。
        返回值:
            无，实例化后保存连接参数并延迟建立连接。
        """
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self._connection = None

    @classmethod
    def from_settings(cls, host: str | None = None, port: int | None = None):
        """从环境配置创建只读 MySQL 客户端。

        请求参数:
            host: 可选 host 覆盖值，SSH tunnel 场景会传入本地 host。
            port: 可选 port 覆盖值，SSH tunnel 场景会传入本地端口。
        返回值:
            ReadOnlyMySQLClient 实例。
        """
        return cls(
            host=host or settings.factor_db_host,
            port=port or settings.factor_db_port,
            database=settings.factor_db_name,
            user=settings.factor_db_user,
            password=settings.factor_db_password,
        )

    @staticmethod
    def sql_code_view(sql: str) -> str:
        """把 SQL 中字符串和注释内容替换为空白，便于做只读语句校验。

        请求参数:
            sql: 原始 SQL 文本。
        返回值:
            保留 SQL 代码位置、隐藏字符串和注释内容后的 SQL 文本。
        """
        chars = list(sql)
        i = 0
        quote: str | None = None

        while i < len(chars):
            char = chars[i]

            if quote is not None:
                chars[i] = " "
                if char == "\\" and quote in {"'", '"'} and i + 1 < len(chars):
                    i += 1
                    chars[i] = " "
                elif char == quote:
                    if i + 1 < len(chars) and chars[i + 1] == quote:
                        i += 1
                        chars[i] = " "
                    else:
                        quote = None
                i += 1
                continue

            if char in {"'", '"', "`"}:
                quote = char
                chars[i] = " "
                i += 1
                continue

            if (
                char == "-"
                and i + 2 < len(chars)
                and chars[i + 1] == "-"
                and chars[i + 2].isspace()
            ):
                chars[i] = " "
                i += 1
                chars[i] = " "
                i += 1
                while i < len(chars) and chars[i] != "\n":
                    chars[i] = " "
                    i += 1
                continue

            if char == "#":
                chars[i] = " "
                i += 1
                while i < len(chars) and chars[i] != "\n":
                    chars[i] = " "
                    i += 1
                continue

            if char == "/" and i + 1 < len(chars) and chars[i + 1] == "*":
                chars[i] = " "
                i += 1
                chars[i] = " "
                i += 1
                while i < len(chars):
                    end_of_comment = chars[i] == "*" and i + 1 < len(chars) and chars[i + 1] == "/"
                    chars[i] = " "
                    if end_of_comment:
                        i += 1
                        chars[i] = " "
                        i += 1
                        break
                    i += 1
                continue

            i += 1

        return "".join(chars)

    @staticmethod
    def ensure_select_only(sql: str) -> None:
        """校验 SQL 只能是单条 SELECT 或 WITH 查询。

        请求参数:
            sql: 待执行 SQL 文本。
        返回值:
            无；非只读查询会抛出 ValueError。
        """
        statement = ReadOnlyMySQLClient.sql_code_view(sql).lstrip()
        statement_without_trailing_semicolon = statement.rstrip()
        if statement_without_trailing_semicolon.endswith(";"):
            statement_without_trailing_semicolon = statement_without_trailing_semicolon[:-1].rstrip()
        if (
            not statement_without_trailing_semicolon
            or ";" in statement_without_trailing_semicolon
            or not re.match(r"^(select|with)\b", statement_without_trailing_semicolon, re.IGNORECASE)
            or MUTATING_SQL_RE.search(statement_without_trailing_semicolon)
            or INTO_FILE_SQL_RE.search(statement_without_trailing_semicolon)
            or LOCK_FUNCTION_SQL_RE.search(statement_without_trailing_semicolon)
        ):
            raise ValueError("Only SELECT statements are allowed")

    def connect(self):
        """建立或复用 MySQL 连接。

        请求参数:
            无，使用当前实例保存的连接参数。
        返回值:
            pymysql Connection 对象。
        """
        if self._connection is None:
            self._connection = pymysql.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
                charset="utf8mb4",
                autocommit=True,
                cursorclass=DictCursor,
                connect_timeout=10,
                read_timeout=30,
                write_timeout=30,
            )
        return self._connection

    def fetch_all(self, sql: str, params: Sequence[Any] | dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """执行只读查询并返回全部结果。

        请求参数:
            sql: SELECT 或 WITH 查询语句。
            params: SQL 参数，支持序列或字典。
        返回值:
            查询结果列表，每行是字段名到字段值的字典。
        """
        self.ensure_select_only(sql)
        connection = self.connect()
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return list(cursor.fetchall())

    def fetch_one(self, sql: str, params: Sequence[Any] | dict[str, Any] | None = None) -> dict[str, Any] | None:
        """执行只读查询并返回第一行结果。

        请求参数:
            sql: SELECT 或 WITH 查询语句。
            params: SQL 参数，支持序列或字典。
        返回值:
            第一行结果字典；没有结果时返回 None。
        """
        self.ensure_select_only(sql)
        connection = self.connect()
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone()

    def close(self) -> None:
        """关闭当前 MySQL 连接。

        请求参数:
            无。
        返回值:
            无，连接关闭后内部连接对象会置空。
        """
        if self._connection is not None:
            self._connection.close()
            self._connection = None
