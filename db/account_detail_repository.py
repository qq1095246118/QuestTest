"""账户详情测试源数据的数据库访问层。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from db.client import DatabaseClient


class AccountDetailFixtureRepository:
    """管理测试环境自有账户、快照、批次和读视图资源。"""

    def __init__(self, client: DatabaseClient, *, environment: str) -> None:
        """初始化 Repository；测试源数据禁止在其他环境写入。"""

        if environment != "test":
            raise ValueError("Account detail fixtures require the test environment")
        self.client = client

    @staticmethod
    def _require_fixture_id(fixture_id: str) -> None:
        """校验只允许本次测试生成的 UUID 标识。"""

        if not re.fullmatch(r"qad_[0-9a-f]{32}", fixture_id):
            raise ValueError("fixture_id must be a QuestTest UUID")

    @staticmethod
    def _require_view_id(view_id: str) -> None:
        """校验账户摘要接口使用的 read snapshot 视图标识。"""

        if not re.fullmatch(r"read_[0-9a-f]{32}", view_id):
            raise ValueError("view_id must be a read snapshot UUID")

    def create_account(self, fixture_id: str, actor_id: int) -> int:
        """创建无交易所凭据和 NT 映射的专用账户。"""

        self._require_fixture_id(fixture_id)
        now = datetime.now()
        with self.client.transaction() as transaction:
            result = transaction.execute(
                """INSERT INTO quant_accounts
                (exchange,email,admin_id,api_key,secret_key,api_description,status,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                ("questtest_fixture", fixture_id + "@example.invalid", actor_id, "", "", fixture_id, "active", now, now),
            )
            if not result.lastrowid:
                raise RuntimeError("account fixture insert did not return an ID")
            account_id = int(result.lastrowid)
            transaction.execute(
                """INSERT INTO quant_account_profiles
                (account_id,name,business_owner_id,created_by,status,version,created_at,updated_at,account_code,default_risk_config)
                VALUES (%s,%s,%s,%s,%s,1,%s,%s,%s,%s)""",
                (account_id, fixture_id, actor_id, actor_id, "active", now, now, fixture_id, '{"enabled":false}'),
            )
        return account_id

    def insert_sources(
        self,
        fixture_id: str,
        account_id: int,
        snapshot_payload: dict[str, Any],
        positions: list[dict[str, Any]],
    ) -> None:
        """插入账户快照、分钟批次和持仓原始事实，不写派生指标。"""

        self._require_fixture_id(fixture_id)
        now = datetime.now()
        observed_at = now
        cutoff = now - timedelta(minutes=1)
        snapshot_id = fixture_id + "_snapshot"
        batch_id = fixture_id + "_batch"
        snapshot = dict(snapshot_payload)
        snapshot["field_status"] = {
            key: {"status": "available" if value is not None else "unavailable",
                  "reason_code": None if value is not None else "SOURCE_FIELD_UNAVAILABLE"}
            for key, value in snapshot.items()
        }
        current_data = {
            "states": [],
            "positions": [],
            "position_batches": [],
            "account_source": {},
            "account_positions": {str(account_id): positions},
        }
        manifest = {"accounts": [snapshot_id], "strategies": []}
        with self.client.transaction() as transaction:
            transaction.execute(
                """INSERT INTO quant_account_snapshots
                (id,created_at,updated_at,version,account_id,observed_at,source_key,source_watermark,data_status,payload)
                VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s)""",
                (snapshot_id, now, now, account_id, observed_at, fixture_id, fixture_id, "complete", json.dumps(snapshot)),
            )
            transaction.execute(
                """INSERT INTO quant_collection_batches
                (id,namespace,cutoff,generation,status,data_status,manifest,sources,current_data,fresh_until,expires_at,published_at,created_at,updated_at)
                VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (batch_id, "quant-minute-test", cutoff, "committed", "complete",
                 json.dumps(manifest), "[]", json.dumps(current_data), now + timedelta(minutes=5),
                 now + timedelta(minutes=5), cutoff, cutoff, cutoff),
            )

    def insert_view(self, fixture_id: str, actor_id: int, account_id: int, view_id: str) -> None:
        """创建引用本次批次、快照和账户身份的账户读取视图。"""

        self._require_fixture_id(fixture_id)
        self._require_view_id(view_id)
        now = datetime.now()
        utc_now = datetime.now(timezone.utc)
        as_of = utc_now.isoformat().replace("+00:00", "Z")
        fresh_until = utc_now + timedelta(minutes=5)
        expires_at = utc_now + timedelta(minutes=15)
        account = {
            "id": account_id,
            "email": fixture_id + "@example.invalid",
            "status": "active",
            "admin_id": actor_id,
            "exchange": "questtest_fixture",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        profile = {
            "AccountID": account_id,
            "Name": fixture_id,
            "Status": "active",
            "Version": 1,
            "CreatedBy": actor_id,
            "BusinessOwnerID": actor_id,
            "AccountCode": fixture_id,
            "CreatedAt": now.isoformat(),
            "UpdatedAt": now.isoformat(),
            "DefaultRiskConfig": {"enabled": False},
        }
        payload = {
            "version": 1,
            "batch_id": fixture_id + "_batch",
            "business": {
                "accounts": [account],
                "profiles": [profile],
                "assignments": [],
                "definitions": [],
                "positions": None,
                "position_batches": None,
                "snapshots": None,
                "bills": [],
                "receipts": [],
                "policies": [],
                "cycles": [],
                "events": [],
                "coverage": [],
                "account_access": [],
                "users": [],
            },
            "account_snapshot_ids": [fixture_id + "_snapshot"],
            "strategy_sample_ids": [],
        }
        context = {
            "minute_mode": True,
            "minute_endpoint_positions": True,
            "selection_members": None,
            "minute_missing_runs": {},
            "minute_missing_accounts": {},
            "minute_current_statistics": {},
            "minute_meta": {
                "generation": fixture_id + "_batch",
                "data_status": "complete",
                "as_of": as_of,
                "fresh_until": fresh_until.isoformat().replace("+00:00", "Z"),
                "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
                "source_observed_at": as_of,
                "sources": [],
                "reason_code": None,
            },
            "questtest_fixture_id": fixture_id,
        }
        self.client.execute(
            """INSERT INTO quant_read_views
            (id,actor_id,account_ids,context,payload,created_at,expires_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (view_id, actor_id, json.dumps([account_id]), json.dumps(context), json.dumps(payload), now, now + timedelta(minutes=15)),
        )

    def cleanup(self, fixture_id: str, actor_id: int, account_id: int, view_id: str) -> None:
        """按 fixture、所有者和账户身份清理本次资源并验证零残留。"""

        self._require_fixture_id(fixture_id)
        self._require_view_id(view_id)
        with self.client.transaction() as transaction:
            view = transaction.fetch_one("SELECT actor_id,context FROM quant_read_views WHERE id=%s FOR UPDATE", (view_id,))
            if view is not None and view["actor_id"] != actor_id:
                raise ValueError("account detail view owner mismatch")
            transaction.execute("DELETE FROM quant_read_pages WHERE view_id=%s", (view_id,))
            transaction.execute("DELETE FROM quant_read_views WHERE id=%s AND actor_id=%s", (view_id, actor_id))
            transaction.execute("DELETE FROM quant_account_snapshots WHERE id=%s AND account_id=%s AND source_key=%s", (fixture_id + "_snapshot", account_id, fixture_id))
            transaction.execute("DELETE FROM quant_collection_batches WHERE id=%s AND namespace=%s", (fixture_id + "_batch", "quant-minute-test"))
            profile = transaction.fetch_one("SELECT name,created_by FROM quant_account_profiles WHERE account_id=%s FOR UPDATE", (account_id,))
            if profile is not None and profile != {"name": fixture_id, "created_by": actor_id}:
                raise ValueError("account detail profile ownership mismatch")
            transaction.execute("DELETE FROM quant_account_profiles WHERE account_id=%s AND name=%s AND created_by=%s", (account_id, fixture_id, actor_id))
            transaction.execute("DELETE FROM quant_accounts WHERE id=%s AND api_description=%s AND admin_id=%s", (account_id, fixture_id, actor_id))
        self.assert_clean(fixture_id, account_id, view_id)

    def assert_clean(self, fixture_id: str, account_id: int, view_id: str) -> None:
        """读取本次资源相关表，残留任一记录时抛出异常。"""

        self._require_fixture_id(fixture_id)
        self._require_view_id(view_id)
        checks = (
            ("SELECT COUNT(*) AS n FROM quant_accounts WHERE id=%s", (account_id,)),
            ("SELECT COUNT(*) AS n FROM quant_account_profiles WHERE account_id=%s", (account_id,)),
            ("SELECT COUNT(*) AS n FROM quant_account_snapshots WHERE source_key=%s", (fixture_id,)),
            ("SELECT COUNT(*) AS n FROM quant_collection_batches WHERE id=%s", (fixture_id + "_batch",)),
            ("SELECT COUNT(*) AS n FROM quant_read_views WHERE id=%s", (view_id,)),
            ("SELECT COUNT(*) AS n FROM quant_read_pages WHERE view_id=%s", (view_id,)),
        )
        with self.client.transaction() as transaction:
            for query, params in checks:
                row = transaction.fetch_one(query, params)
                if row is None or int(row["n"]) != 0:
                    raise RuntimeError("account detail fixture still has residual data")
