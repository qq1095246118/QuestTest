"""为真实计算查询准备自有输入快照；不修改已有账户或最终计算结果。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

from db.client import DatabaseClient


class SourceFixtureRepository:
    """只管理 UUID 标识的专用身份、原始输入和查询视图资源。"""

    def __init__(self, client: DatabaseClient, *, environment: str) -> None:
        """接收测试库客户端；非测试环境拒绝构造。"""
        if environment != "test":
            raise ValueError("Source fixtures require the test environment")
        self.client = client

    @staticmethod
    def _require_id(view_id: str) -> None:
        if not re.fullmatch(r"qfi_[0-9a-f]{32}", view_id):
            raise ValueError("Only owned UUID fixture identifiers are accepted")

    def insert(self, view_id: str, actor_id: int, account_id: int,
               payload: dict[str, Any], context: dict[str, Any]) -> None:
        """创建全新的原始输入快照；主键冲突报错，不覆盖任何现有数据。"""
        self._require_id(view_id)
        now = datetime.now()
        tagged_context = dict(context, questtest_fixture_id=view_id)
        self.client.execute(
            "INSERT INTO quant_read_views "
            "(id,actor_id,account_ids,context,payload,created_at,expires_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (view_id, actor_id, json.dumps([account_id]), json.dumps(tagged_context),
             json.dumps(payload), now, now + timedelta(minutes=5)),
        )

    def create_account(self, fixture_id: str, actor_id: int) -> int:
        """创建无交易所凭据、无 NT 映射的独立测试身份，返回数据库分配 ID。"""
        self._require_id(fixture_id)
        now = datetime.now()
        with self.client.transaction() as tx:
            result = tx.execute(
                "INSERT INTO quant_accounts (exchange,email,admin_id,api_key,secret_key,api_description,status,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ("questtest_fixture", fixture_id + "@example.invalid", actor_id, "", "", fixture_id, "active", now, now),
            )
            if not result.lastrowid:
                raise RuntimeError("Test identity ID was not returned")
            account_id = int(result.lastrowid)
            tx.execute(
                "INSERT INTO quant_account_profiles "
                "(account_id,name,business_owner_id,created_by,status,version,created_at,updated_at,account_code,default_risk_config) "
                "VALUES (%s,%s,%s,%s,%s,1,%s,%s,%s,%s)",
                (account_id, fixture_id, actor_id, actor_id, "active", now, now, fixture_id, '{"enabled":false}'),
            )
        return account_id

    def delete_account(self, fixture_id: str, actor_id: int, account_id: int) -> dict[str, int]:
        """仅删除名称、归属及创建状态均匹配的专用身份；不接触既有账户。"""
        self._require_id(fixture_id)
        with self.client.transaction() as tx:
            row = tx.fetch_one(
                "SELECT email,exchange,admin_id,status,api_description FROM quant_accounts WHERE id=%s FOR UPDATE", (account_id,),
            )
            if row is None:
                return {"accounts": 0, "profiles": 0}
            if row != {"email": fixture_id + "@example.invalid", "exchange": "questtest_fixture",
                       "admin_id": actor_id, "status": "active", "api_description": fixture_id}:
                raise ValueError("Test account ownership mismatch; identity retained")
            profile = tx.fetch_one("SELECT name,created_by,active_nt_account_id FROM quant_account_profiles WHERE account_id=%s FOR UPDATE", (account_id,))
            if profile != {"name": fixture_id, "created_by": actor_id, "active_nt_account_id": None}:
                raise ValueError("Test profile changed; identity retained")
            profiles = tx.execute("DELETE FROM quant_account_profiles WHERE account_id=%s AND name=%s", (account_id, fixture_id))
            accounts = tx.execute("DELETE FROM quant_accounts WHERE id=%s AND api_description=%s", (account_id, fixture_id))
        return {"accounts": accounts.rowcount, "profiles": profiles.rowcount}

    def insert_sources(self, fixture_id: str, account_id: int, snapshot: dict[str, Any],
                       positions: list[dict[str, Any]]) -> None:
        """插入自有历史 cutoff 的原始分钟输入；随后必须检查共享批次选择未命中此行。"""
        self._require_id(fixture_id)
        now = datetime.now()
        batch_id = fixture_id + "_batch"
        inactive_cutoff = now - timedelta(days=1)
        observed_at = now
        snapshot_id = snapshot["ID"]
        if snapshot_id != fixture_id + "_snapshot" or snapshot["AccountID"] != account_id:
            raise ValueError("Source snapshot identity does not belong to this fixture")
        manifest = {"accounts": [snapshot_id], "strategies": []}
        current_data = {"states": [], "positions": [], "position_batches": [],
                        "account_source": {}, "account_positions": {str(account_id): positions}}
        with self.client.transaction() as tx:
            tx.execute(
                "INSERT INTO quant_account_snapshots "
                "(id,created_at,updated_at,version,account_id,observed_at,source_key,source_watermark,data_status,payload) "
                "VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s)",
                (snapshot_id, now, now, account_id, observed_at, fixture_id, fixture_id, "complete", json.dumps(snapshot["Payload"])),
            )
            tx.execute(
                "INSERT INTO quant_collection_batches "
                "(id,namespace,cutoff,generation,status,data_status,manifest,sources,current_data,fresh_until,expires_at,published_at,created_at,updated_at) "
                "VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (batch_id, "quant-minute-test", inactive_cutoff, "committed", "complete", json.dumps(manifest), "[]",
                 json.dumps(current_data), now + timedelta(minutes=5), now + timedelta(minutes=5), inactive_cutoff, inactive_cutoff, inactive_cutoff),
            )

    def delete_sources(self, fixture_id: str, account_id: int) -> dict[str, int]:
        """仅清理本次 namespace、原始快照 ID 与账户三者共同限定的数据。"""
        self._require_id(fixture_id)
        with self.client.transaction() as tx:
            batches = tx.execute("DELETE FROM quant_collection_batches WHERE id=%s AND namespace=%s", (fixture_id + "_batch", "quant-minute-test"))
            snapshots = tx.execute("DELETE FROM quant_account_snapshots WHERE id=%s AND account_id=%s AND source_key=%s", (fixture_id + "_snapshot", account_id, fixture_id))
        return {"batches": batches.rowcount, "snapshots": snapshots.rowcount}

    def assert_clean(self, fixture_id: str, account_id: int) -> None:
        """重新读取六张表，验证本次全部资源清空；有残留则报错。"""
        self._require_id(fixture_id)
        checks = (
            ("SELECT COUNT(*) AS n FROM quant_accounts WHERE id=%s", (account_id,)),
            ("SELECT COUNT(*) AS n FROM quant_account_profiles WHERE account_id=%s", (account_id,)),
            ("SELECT COUNT(*) AS n FROM quant_account_snapshots WHERE source_key=%s", (fixture_id,)),
            ("SELECT COUNT(*) AS n FROM quant_collection_batches WHERE id=%s", (fixture_id + "_batch",)),
            ("SELECT COUNT(*) AS n FROM quant_read_views WHERE id=%s", (fixture_id,)),
            ("SELECT COUNT(*) AS n FROM quant_read_pages WHERE view_id=%s", (fixture_id,)),
        )
        with self.client.transaction() as tx:
            for query, params in checks:
                row = tx.fetch_one(query, params)
                if row is None or row["n"] != 0:
                    raise RuntimeError("Source fixture still has residual data")

    def assert_not_current_batch(self, fixture_id: str) -> None:
        """检查常用批次选择维度均未选中本次测试行；不是后台选择代码的替代证据。"""
        self._require_id(fixture_id)
        with self.client.transaction() as tx:
            for column in ("cutoff", "created_at", "published_at", "generation"):
                row = tx.fetch_one(
                    f"SELECT id FROM quant_collection_batches WHERE namespace=%s AND status=%s ORDER BY {column} DESC LIMIT 1",
                    ("quant-minute-test", "committed"),
                )
                if row is None or row["id"] == fixture_id + "_batch":
                    raise RuntimeError("Test batch became a shared current candidate")

    def delete(self, view_id: str, actor_id: int) -> dict[str, int]:
        """核对 ID、所有者和创建标记后清理自有输入和分页；不匹配时拒绝删除。"""
        self._require_id(view_id)
        with self.client.transaction() as tx:
            row = tx.fetch_one(
                "SELECT actor_id,context FROM quant_read_views WHERE id=%s FOR UPDATE",
                (view_id,),
            )
            if row is None:
                return {"views": 0, "pages": 0}
            context = row["context"]
            if isinstance(context, str):
                context = json.loads(context)
            if row["actor_id"] != actor_id or context.get("questtest_fixture_id") != view_id:
                raise ValueError("Fixture ownership mismatch; nothing was deleted")
            pages = tx.execute("DELETE FROM quant_read_pages WHERE view_id=%s", (view_id,))
            views = tx.execute("DELETE FROM quant_read_views WHERE id=%s AND actor_id=%s", (view_id, actor_id))
        remaining = self.client.fetch_one(
            "SELECT COUNT(*) AS n FROM quant_read_views WHERE id=%s", (view_id,),
        )
        if remaining is None or remaining["n"] != 0:
            raise RuntimeError("Fixture cleanup verification failed")
        return {"views": views.rowcount, "pages": pages.rowcount}
