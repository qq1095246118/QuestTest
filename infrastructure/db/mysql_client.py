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


def _strip_leading_comments(sql: str) -> str:
    stripped = sql.lstrip()
    while True:
        if stripped.startswith("--"):
            line_end = stripped.find("\n")
            if line_end == -1:
                return ""
            stripped = stripped[line_end + 1 :].lstrip()
            continue
        if stripped.startswith("/*"):
            comment_end = stripped.find("*/")
            if comment_end == -1:
                return ""
            stripped = stripped[comment_end + 2 :].lstrip()
            continue
        return stripped


def ensure_select_only(sql: str) -> None:
    statement = _strip_leading_comments(sql)
    if (
        not statement
        or ";" in statement
        or not re.match(r"^(select|with)\b", statement, re.IGNORECASE)
        or MUTATING_SQL_RE.search(statement)
    ):
        raise ValueError("Only SELECT statements are allowed")


class ReadOnlyMySQLClient:
    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
    ) -> None:
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self._connection = None

    @classmethod
    def from_settings(cls, endpoint=None):
        db_host = endpoint.host if endpoint is not None else settings.factor_db_host
        db_port = endpoint.port if endpoint is not None else settings.factor_db_port
        return cls(
            host=db_host,
            port=db_port,
            database=settings.factor_db_name,
            user=settings.factor_db_user,
            password=settings.factor_db_password,
        )

    def connect(self):
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
        ensure_select_only(sql)
        connection = self.connect()
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return list(cursor.fetchall())

    def fetch_one(self, sql: str, params: Sequence[Any] | dict[str, Any] | None = None) -> dict[str, Any] | None:
        ensure_select_only(sql)
        connection = self.connect()
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
