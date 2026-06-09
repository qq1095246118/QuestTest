from __future__ import annotations

from types import SimpleNamespace

import pytest

from infrastructure.db.mysql_client import ReadOnlyMySQLClient, ensure_select_only
from infrastructure.db.ssh_tunnel import DatabaseEndpoint, open_database_endpoint


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM factor",
        "  select id FROM factor WHERE id = %s",
        "WITH latest_factor AS (SELECT * FROM factor) SELECT * FROM latest_factor",
    ],
)
def test_ensure_select_only_accepts_select_and_with(sql):
    ensure_select_only(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE factor SET name = 'x'",
        "DELETE FROM factor WHERE id = 1",
        "INSERT INTO factor (id) VALUES (1)",
        "DROP TABLE factor",
        "SELECT * FROM factor; DELETE FROM factor",
    ],
)
def test_ensure_select_only_rejects_mutating_or_multi_statement_sql(sql):
    with pytest.raises(ValueError, match="Only SELECT statements are allowed"):
        ensure_select_only(sql)


def test_read_only_mysql_client_fetch_all_uses_dict_cursor_and_params(monkeypatch):
    calls = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def execute(self, sql, params):
            calls["sql"] = sql
            calls["params"] = params

        def fetchall(self):
            return [{"id": 1, "name": "factor"}]

    class FakeConnection:
        def cursor(self):
            calls["cursor_called"] = True
            return FakeCursor()

    def fake_connect(**kwargs):
        calls["connect_kwargs"] = kwargs
        return FakeConnection()

    monkeypatch.setattr("infrastructure.db.mysql_client.pymysql.connect", fake_connect)

    client = ReadOnlyMySQLClient(
        host="127.0.0.1",
        port=3306,
        database="factor_db",
        user="factor_app",
        password="secret",
    )

    rows = client.fetch_all("SELECT * FROM factor WHERE id = %s", params=(1,))

    assert rows == [{"id": 1, "name": "factor"}]
    assert calls["sql"] == "SELECT * FROM factor WHERE id = %s"
    assert calls["params"] == (1,)
    assert calls["cursor_called"] is True
    assert calls["connect_kwargs"]["cursorclass"].__name__ == "DictCursor"
    assert calls["connect_kwargs"]["charset"] == "utf8mb4"
    assert calls["connect_kwargs"]["autocommit"] is True
    assert calls["connect_kwargs"]["connect_timeout"] == 10
    assert calls["connect_kwargs"]["read_timeout"] == 30
    assert calls["connect_kwargs"]["write_timeout"] == 30


def test_open_database_endpoint_yields_direct_endpoint_when_ssh_disabled():
    settings = SimpleNamespace(
        factor_db_host="db.internal",
        factor_db_port=3307,
        factor_ssh_enabled=False,
    )

    with open_database_endpoint(settings) as endpoint:
        assert endpoint == DatabaseEndpoint(host="db.internal", port=3307)
