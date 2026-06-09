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


def _sql_code_view(sql: str) -> str:
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

        if char == "-" and i + 1 < len(chars) and chars[i + 1] == "-":
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


def ensure_select_only(sql: str) -> None:
    statement = _sql_code_view(sql).lstrip()
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
    def from_settings(cls, host: str | None = None, port: int | None = None):
        return cls(
            host=host or settings.factor_db_host,
            port=port or settings.factor_db_port,
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
