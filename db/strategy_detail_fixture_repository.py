"""策略详情真实接口测试的测试环境读视图 fixture。"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any
from db.client import DatabaseClient


class StrategyDetailFixtureRepository:
    """创建策略详情所需的隔离 read view；仅允许测试环境写入。"""

    def __init__(self, client: DatabaseClient, *, environment: str) -> None:
        if environment != "test":
            raise ValueError("Strategy detail fixtures require the test environment")
        self.client = client

    @staticmethod
    def _check_fixture_id(fixture_id: str) -> None:
        if not re.fullmatch(r"qsd_[0-9a-f]{32}", fixture_id):
            raise ValueError("invalid strategy fixture id")

    @staticmethod
    def _check_view_id(view_id: str) -> None:
        if not re.fullmatch(r"read_[0-9a-f]{32}", view_id):
            raise ValueError("invalid strategy read view id")

    def create_account(self, fixture_id: str, actor_id: int, index: int) -> int:
        """创建本次策略 fixture 专用账户，供 read view 做权限校验。"""
        self._check_fixture_id(fixture_id)
        if index < 0:
            raise ValueError("account fixture index must be non-negative")
        account_tag = f"{fixture_id}_account_{index}"
        now = datetime.now()
        with self.client.transaction() as tx:
            result = tx.execute(
                """INSERT INTO quant_accounts
                (exchange,email,admin_id,api_key,secret_key,api_description,status,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                ("questtest_fixture", account_tag + "@example.invalid", actor_id, "", "", account_tag,
                 "active", now, now),
            )
            if not result.lastrowid:
                raise RuntimeError("strategy detail account fixture insert did not return an ID")
            account_id = int(result.lastrowid)
            tx.execute(
                """INSERT INTO quant_account_profiles
                (account_id,name,business_owner_id,created_by,status,active_nt_account_id,version,
                 created_at,updated_at,account_code,default_risk_config)
                VALUES (%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s)""",
                (account_id, account_tag, actor_id, actor_id, "active", account_tag, now, now,
                 account_tag, '{"enabled":false}'),
            )
        return account_id

    def find_existing_strategy(self) -> dict[str, Any] | None:
        """读取一个测试环境中已经存在、可用于真实详情请求的策略。

        该查询只用于发现真实 subject，不创建或修改任何业务数据。返回策略定义的
        ``id``、``code``、``name`` 和 ``status``；没有可见策略时返回 ``None``，由
        上层 live case 将环境能力明确标记为 skipped。
        """

        return self.client.fetch_one(
            """SELECT d.id, d.code, d.name, d.status
               FROM quant_strategy_definitions AS d
               WHERE d.status IN ('active', 'running')
                 AND d.code NOT LIKE 'qsd_%'
                 AND EXISTS (
                     SELECT 1
                     FROM quant_strategy_account_runs AS r
                     WHERE r.definition_id = d.id
                       AND r.status IN ('active', 'running')
                 )
               ORDER BY d.updated_at DESC, d.id
               LIMIT 1"""
        )

    def create_view(self, fixture_id: str, view_id: str, actor_id: int, strategy_id: str,
                    accounts: list[dict[str, Any]], metrics: dict[str, Any]) -> None:
        """写入定义、归属、指标和交易资源的统一测试视图。"""
        self._check_fixture_id(fixture_id)
        self._check_view_id(view_id)
        now = datetime.now()
        definition = {
            "id": strategy_id,
            "code": fixture_id,
            "name": strategy_id,
            "status": "active",
            "researcher_id": actor_id,
            "created_by": actor_id,
        }
        runs: list[dict[str, Any]] = []
        with self.client.transaction() as tx:
            tx.execute(
                """INSERT INTO quant_strategy_definitions
                (id,created_at,updated_at,version,category,code,name,researcher_id,created_by,status)
                VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s)""",
                (strategy_id, now, now, "questtest", fixture_id, strategy_id, actor_id, actor_id, "active"),
            )
            for index, row in enumerate(accounts):
                account_id = int(row["account_id"])
                run_id = f"{fixture_id}_run_{index}"
                nt_account_id = f"{fixture_id}_nt_account_{index}"
                assignment_id = f"{fixture_id}:assignment:{index}"
                run = {
                    "id": run_id,
                    "account_id": account_id,
                    "definition_id": strategy_id,
                    "status": "active",
                    "started_at": now.isoformat(),
                    "ended_at": None,
                    "nt_strategy_id": strategy_id,
                    "nt_account_id": nt_account_id,
                }
                runs.append(run)
                tx.execute(
                    """INSERT INTO quant_strategy_account_runs
                    (id,created_at,updated_at,version,strategy_name,account_id,definition_id,
                     researcher_id,researcher_name,business_owner_id,business_owner_name,status,
                     started_at,ended_at,end_reason,nt_strategy_id,nt_account_id,legacy_row_id,
                     risk_config,runtime,active_key)
                    VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (run_id, now, now, strategy_id, account_id, strategy_id, actor_id, "questtest",
                     actor_id, "questtest", "active", now, None, None, strategy_id, nt_account_id,
                     None, json.dumps({"enabled": False}), json.dumps({"actual_status": "running"}),
                     f"{account_id}:{strategy_id}"),
                )
                tx.execute(
                    """INSERT INTO quant_nt_strategy_accounts
                    (nt_strategy_id,nt_account_id,exchange_id,enabled,testnet,source_updated_at,
                     source_status,sync_generation,synced_at)
                    VALUES (%s,%s,%s,1,1,%s,%s,%s,%s)""",
                    (strategy_id, nt_account_id, "questtest_fixture", now, "active", fixture_id, now),
                )
                tx.execute(
                    """INSERT INTO quant_nt_account_strategy_assignments
                    (nt_assignment_id,nt_strategy_id,nt_account_id,exchange_id,migration_id,
                     effective_from,effective_to,status,source_payload,source_status,sync_generation,synced_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (assignment_id, strategy_id, nt_account_id, "questtest_fixture", None, now, None,
                     "ACTIVE", json.dumps({"assignment_id": assignment_id, "strategy_id": strategy_id,
                                            "account_id": nt_account_id, "status": "ACTIVE"}),
                     "active", fixture_id, now),
                )
            batch_id = fixture_id + "_batch"
            manifest = {
                "accounts": [str(row["account_id"]) for row in accounts],
                "strategies": [strategy_id],
            }
            current_data = {
                "account_source": {"fixture_id": fixture_id},
                "strategy_source": {"fixture_id": fixture_id, "strategy_id": strategy_id},
                "states": [],
                "positions": [],
                "position_batches": [],
                "account_positions": {str(row["account_id"]): [] for row in accounts},
            }
            tx.execute(
                """INSERT INTO quant_collection_batches
                (id,namespace,cutoff,generation,status,data_status,manifest,sources,current_data,
                 fresh_until,expires_at,published_at,created_at,updated_at)
                VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (batch_id, "quant-minute-test", now, "committed", "complete", json.dumps(manifest),
                 "[]", json.dumps(current_data), now + timedelta(minutes=5), now + timedelta(minutes=5),
                 now, now, now),
            )
        payload = {"version": 1, "batch_id": fixture_id + "_batch", "business": {
            "accounts": accounts, "profiles": [], "assignments": runs,
            "definitions": [definition],
            "strategy_samples": metrics.get("samples", []), "strategy_metrics": metrics,
            "positions": metrics.get("positions", []), "signals": metrics.get("signals", []),
            "signal_batches": metrics.get("signal_batches", []),
            "rebalance_plans": metrics.get("rebalance_plans", []),
            "order_intents": metrics.get("order_intents", []),
            "orders": metrics.get("orders", []), "fills": metrics.get("fills", []),
            "bills": [], "receipts": [], "cycles": [], "events": [],
        }, "strategy_sample_ids": [fixture_id + "_sample"]}
        context = {
            "minute_mode": False,
            "selection_members": [str(row["account_id"]) for row in accounts],
            "strategy_members": [strategy_id],
            "minute_meta": {"generation": fixture_id + "_batch", "data_status": "complete", "reason_code": None},
            "questtest_fixture_id": fixture_id,
        }
        self.client.execute("""INSERT INTO quant_read_views
            (id,actor_id,account_ids,context,payload,created_at,expires_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (view_id, actor_id, json.dumps([a.get("id") for a in accounts]),
             json.dumps(context), json.dumps(payload), now, now + timedelta(minutes=15)))

    def cleanup(self, fixture_id: str, view_id: str, actor_id: int,
                account_ids: list[int] | None = None) -> None:
        """删除本次 fixture 的视图、专用账户及分页资源，并确认无残留。"""
        self._check_fixture_id(fixture_id)
        self._check_view_id(view_id)
        with self.client.transaction() as tx:
            tx.execute("DELETE FROM quant_read_pages WHERE view_id=%s", (view_id,))
            tx.execute("DELETE FROM quant_read_views WHERE id=%s AND actor_id=%s", (view_id, actor_id))
            tx.execute("DELETE FROM quant_nt_account_strategy_assignments WHERE sync_generation=%s", (fixture_id,))
            tx.execute("DELETE FROM quant_nt_strategy_accounts WHERE sync_generation=%s", (fixture_id,))
            tx.execute("DELETE FROM quant_strategy_account_runs WHERE id LIKE %s", (fixture_id + "_run_%",))
            tx.execute("DELETE FROM quant_strategy_definitions WHERE code=%s AND created_by=%s", (fixture_id, actor_id))
            tx.execute("DELETE FROM quant_collection_batches WHERE id=%s AND namespace=%s", (fixture_id + "_batch", "quant-minute-test"))
            for index, account_id in enumerate(account_ids or []):
                account_tag = f"{fixture_id}_account_{index}"
                profile = tx.fetch_one(
                    "SELECT name,created_by FROM quant_account_profiles WHERE account_id=%s FOR UPDATE",
                    (account_id,),
                )
                if profile is not None and profile != {"name": account_tag, "created_by": actor_id}:
                    raise ValueError("strategy detail account fixture ownership mismatch")
                tx.execute(
                    "DELETE FROM quant_account_profiles WHERE account_id=%s AND name=%s AND created_by=%s",
                    (account_id, account_tag, actor_id),
                )
                tx.execute(
                    "DELETE FROM quant_accounts WHERE id=%s AND api_description=%s AND admin_id=%s",
                    (account_id, account_tag, actor_id),
                )
            row = tx.fetch_one("SELECT COUNT(*) AS n FROM quant_read_views WHERE id=%s", (view_id,))
            if row is not None and int(row["n"]) != 0:
                raise RuntimeError("strategy detail fixture still has residual view")
            residual_checks = (
                ("quant_read_pages", "view_id=%s", (view_id,)),
                ("quant_collection_batches", "id=%s", (fixture_id + "_batch",)),
                ("quant_strategy_definitions", "code=%s", (fixture_id,)),
                ("quant_strategy_account_runs", "id LIKE %s", (fixture_id + "_run_%",)),
                ("quant_nt_strategy_accounts", "sync_generation=%s", (fixture_id,)),
                ("quant_nt_account_strategy_assignments", "sync_generation=%s", (fixture_id,)),
            )
            for table, predicate, params in residual_checks:
                residual = tx.fetch_one(f"SELECT COUNT(*) AS n FROM {table} WHERE {predicate}", params)
                if residual is None or int(residual["n"]) != 0:
                    raise RuntimeError(f"strategy detail fixture still has residual {table} data")
        for index, account_id in enumerate(account_ids or []):
            account_tag = f"{fixture_id}_account_{index}"
            account_row = self.client.fetch_one(
                "SELECT COUNT(*) AS n FROM quant_accounts WHERE id=%s AND api_description=%s",
                (account_id, account_tag),
            )
            profile_row = self.client.fetch_one(
                "SELECT COUNT(*) AS n FROM quant_account_profiles WHERE account_id=%s AND name=%s",
                (account_id, account_tag),
            )
            if (account_row and int(account_row["n"])) or (profile_row and int(profile_row["n"])):
                raise RuntimeError("strategy detail account fixture still has residual data")
