from __future__ import annotations

from types import SimpleNamespace

import pytest

from infrastructure.db.mysql_client import ReadOnlyMySQLClient, ensure_select_only
from infrastructure.db.ssh_tunnel import DatabaseEndpoint, open_database_endpoint
from infrastructure.db.factor_library_queries import FactorListQuery, fetch_factor_list_db_page


class RecordingFactorClient:
    def __init__(self, fetch_one_rows=None, fetch_all_rows=None):
        self.fetch_one_rows = list(fetch_one_rows or [])
        self.fetch_all_rows = list(fetch_all_rows or [])
        self.calls = []

    def fetch_one(self, sql, params=None):
        self.calls.append(("fetch_one", sql, params))
        return self.fetch_one_rows.pop(0)

    def fetch_all(self, sql, params=None):
        self.calls.append(("fetch_all", sql, params))
        return self.fetch_all_rows.pop(0)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM factor",
        "SELECT * FROM factor;",
        "  select id FROM factor WHERE id = %s",
        "WITH latest_factor AS (SELECT * FROM factor) SELECT * FROM latest_factor",
        "SELECT * FROM factor WHERE name = 'delete'",
        "SELECT 1 /* update */",
        "SELECT 1 -- update\n",
        "SELECT ';' AS semicolon",
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
        "SELECT * FROM factor INTO OUTFILE '/tmp/x'",
        "SELECT * FROM factor INTO DUMPFILE '/tmp/x'",
        "SELECT GET_LOCK('factor_lock', 1)",
        "SELECT RELEASE_LOCK('factor_lock')",
        "SELECT 1--1; DELETE FROM factor",
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


def test_read_only_mysql_client_fetch_one_returns_row_and_passes_params(monkeypatch):
    calls = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def execute(self, sql, params):
            calls["sql"] = sql
            calls["params"] = params

        def fetchone(self):
            return {"id": 1, "name": "factor"}

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr("infrastructure.db.mysql_client.pymysql.connect", lambda **kwargs: FakeConnection())

    client = ReadOnlyMySQLClient(
        host="127.0.0.1",
        port=3306,
        database="factor_db",
        user="factor_app",
        password="secret",
    )

    row = client.fetch_one("SELECT * FROM factor WHERE id = %(id)s", params={"id": 1})

    assert row == {"id": 1, "name": "factor"}
    assert calls["sql"] == "SELECT * FROM factor WHERE id = %(id)s"
    assert calls["params"] == {"id": 1}


def test_read_only_mysql_client_close_closes_connection_and_resets_state():
    calls = {}

    class FakeConnection:
        def close(self):
            calls["closed"] = True

    client = ReadOnlyMySQLClient(
        host="127.0.0.1",
        port=3306,
        database="factor_db",
        user="factor_app",
        password="secret",
    )
    client._connection = FakeConnection()

    client.close()

    assert calls["closed"] is True
    assert client._connection is None


def test_read_only_mysql_client_from_settings_allows_host_and_port_override(monkeypatch):
    monkeypatch.setattr("infrastructure.db.mysql_client.settings.factor_db_host", "settings-db.internal")
    monkeypatch.setattr("infrastructure.db.mysql_client.settings.factor_db_port", 3306)
    monkeypatch.setattr("infrastructure.db.mysql_client.settings.factor_db_name", "factor_db")
    monkeypatch.setattr("infrastructure.db.mysql_client.settings.factor_db_user", "factor_app")
    monkeypatch.setattr("infrastructure.db.mysql_client.settings.factor_db_password", "secret")

    client = ReadOnlyMySQLClient.from_settings(host="127.0.0.1", port=3307)

    assert client.host == "127.0.0.1"
    assert client.port == 3307
    assert client.database == "factor_db"
    assert client.user == "factor_app"
    assert client.password == "secret"


def test_open_database_endpoint_yields_direct_endpoint_when_ssh_disabled():
    settings = SimpleNamespace(
        factor_db_host="db.internal",
        factor_db_port=3307,
        factor_ssh_enabled=False,
    )

    with open_database_endpoint(settings) as endpoint:
        assert endpoint == DatabaseEndpoint(host="db.internal", port=3307)


def test_open_database_endpoint_starts_and_stops_ssh_tunnel(monkeypatch):
    calls = {}

    class FakeSSHTunnelForwarder:
        local_bind_port = 43307

        def __init__(self, **kwargs):
            calls["kwargs"] = kwargs

        def start(self):
            calls["started"] = True

        def stop(self):
            calls["stopped"] = True

    monkeypatch.setattr("infrastructure.db.ssh_tunnel.SSHTunnelForwarder", FakeSSHTunnelForwarder)
    settings = SimpleNamespace(
        factor_db_host="db.internal",
        factor_db_port=3307,
        factor_ssh_enabled=True,
        factor_ssh_host="ssh.internal",
        factor_ssh_port=22,
        factor_ssh_user="deploy",
        factor_ssh_key_path="/Users/wrh/.ssh/factor.pem",
        factor_ssh_password="secret",
    )

    with open_database_endpoint(settings) as endpoint:
        assert endpoint == DatabaseEndpoint(host="127.0.0.1", port=43307)
        assert calls["started"] is True

    assert calls["kwargs"] == {
        "ssh_address_or_host": ("ssh.internal", 22),
        "remote_bind_address": ("db.internal", 3307),
        "ssh_username": "deploy",
        "ssh_pkey": "/Users/wrh/.ssh/factor.pem",
        "ssh_password": "secret",
    }
    assert calls["stopped"] is True


def test_fetch_factor_list_db_page_queries_and_assembles_page():
    client = RecordingFactorClient(
        fetch_one_rows=[{"total": 2}],
        fetch_all_rows=[
            [{"id": 2}, {"id": 1}],
            [
                {"id": 1, "factor_name": "mean_reversion", "cn_name": "均值回归"},
                {"id": 2, "factor_name": "momentum", "cn_name": "动量"},
            ],
            [
                {"id": 20, "factor_id": 2, "name": "momentum_detail", "status": 1},
                {"id": 10, "factor_id": 1, "name": "mean_reversion_detail", "status": 1},
            ],
            [
                {"factor_id": 2, "id": 8, "theme_key": "momentum", "cn_name": "动量"},
                {"factor_id": 1, "id": 9, "theme_key": "reversal", "cn_name": "反转"},
            ],
        ],
    )

    result = fetch_factor_list_db_page(
        client,
        FactorListQuery(
            page=2,
            limit=10,
            factor_theme="momentum",
            created_by="alice",
            operator_by="bob",
            factor_detail_status=1,
            sort_by="created_at",
            sort_order="asc",
        ),
    )

    assert result == {
        "pagination": {"page": 2, "limit": 10, "total": 2},
        "items": [
            {
                "id": 2,
                "factor_name": "momentum",
                "cn_name": "动量",
                "factor_detail": {"id": 20, "factor_id": 2, "name": "momentum_detail", "status": 1},
                "themes": [{"id": 8, "theme_key": "momentum", "cn_name": "动量"}],
            },
            {
                "id": 1,
                "factor_name": "mean_reversion",
                "cn_name": "均值回归",
                "factor_detail": {"id": 10, "factor_id": 1, "name": "mean_reversion_detail", "status": 1},
                "themes": [{"id": 9, "theme_key": "reversal", "cn_name": "反转"}],
            },
        ],
    }
    assert [call[0] for call in client.calls] == ["fetch_one", "fetch_all", "fetch_all", "fetch_all", "fetch_all"]
    assert "COUNT(DISTINCT f.id)" in client.calls[0][1]
    assert "t.theme_key = %(factor_theme)s" in client.calls[1][1]
    assert "f.created_by = %(created_by)s" in client.calls[1][1]
    assert "f.operator_by = %(operator_by)s" in client.calls[1][1]
    assert "fd.status = %(factor_detail_status)s" in client.calls[1][1]
    assert "SELECT DISTINCT f.id" not in client.calls[1][1]
    assert "AS sort_value" in client.calls[1][1]
    assert "GROUP BY f.id" in client.calls[1][1]
    assert "ORDER BY sort_value ASC, f.id ASC" in client.calls[1][1]
    assert client.calls[1][2]["factor_theme"] == "momentum"
    assert client.calls[1][2]["created_by"] == "alice"
    assert client.calls[1][2]["operator_by"] == "bob"
    assert client.calls[1][2]["factor_detail_status"] == 1
    assert client.calls[1][2]["limit"] == 10
    assert client.calls[1][2]["offset"] == 10


def test_fetch_factor_list_db_page_skips_child_queries_when_page_has_no_ids():
    client = RecordingFactorClient(fetch_one_rows=[{"total": 3}], fetch_all_rows=[[]])

    result = fetch_factor_list_db_page(client, FactorListQuery(page=3, limit=20))

    assert result == {"pagination": {"page": 3, "limit": 20, "total": 3}, "items": []}
    assert [call[0] for call in client.calls] == ["fetch_one", "fetch_all"]


def test_fetch_factor_list_db_page_defaults_unknown_sort_by_to_id():
    client = RecordingFactorClient(fetch_one_rows=[{"total": 0}], fetch_all_rows=[[]])

    fetch_factor_list_db_page(client, FactorListQuery(sort_by="f.created_at; DROP TABLE factors", sort_order="asc"))

    page_sql = client.calls[1][1]
    assert "DROP TABLE" not in page_sql
    assert "f.id AS sort_value" in page_sql
    assert "ORDER BY sort_value ASC, f.id ASC" in page_sql


def test_fetch_factor_list_db_page_defaults_unknown_sort_order_to_desc():
    client = RecordingFactorClient(fetch_one_rows=[{"total": 0}], fetch_all_rows=[[]])

    fetch_factor_list_db_page(client, FactorListQuery(sort_by="cn_name", sort_order="sideways"))

    page_sql = client.calls[1][1]
    assert "ORDER BY sort_value DESC, f.id DESC" in page_sql


def test_fetch_factor_list_db_page_keeps_first_detail_row_for_factor():
    client = RecordingFactorClient(
        fetch_one_rows=[{"total": 1}],
        fetch_all_rows=[
            [{"id": 1}],
            [{"id": 1, "factor_name": "momentum"}],
            [
                {"id": 20, "factor_id": 1, "name": "latest_detail"},
                {"id": 10, "factor_id": 1, "name": "older_detail"},
            ],
            [],
        ],
    )

    result = fetch_factor_list_db_page(client, FactorListQuery())

    assert result["items"][0]["factor_detail"] == {"id": 20, "factor_id": 1, "name": "latest_detail"}
    assert "ORDER BY fd.factor_id ASC, fd.updated_at DESC, fd.id DESC" in client.calls[3][1]


def test_fetch_factor_list_db_page_raises_clear_error_for_missing_factor_row():
    client = RecordingFactorClient(
        fetch_one_rows=[{"total": 2}],
        fetch_all_rows=[
            [{"id": 1}, {"id": 2}],
            [{"id": 1, "factor_name": "momentum"}],
            [],
            [],
        ],
    )

    with pytest.raises(AssertionError, match="Missing factor row for id 2"):
        fetch_factor_list_db_page(client, FactorListQuery())
