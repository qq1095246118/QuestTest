"""账号管理多账号测试 fixture 的数据库访问层。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from db.client import DatabaseClient


class AccountManagementFixtureRepository:
    """只管理本次测试生成的账号、源快照、批次和读取视图。"""

    NAMESPACE = "quant-minute-test"

    def __init__(self, client: DatabaseClient, *, environment: str) -> None:
        """初始化测试环境仓储；生产环境拒绝写入。"""

        if environment != "test":
            raise ValueError("Account management fixtures require the test environment")
        self.client = client

    @staticmethod
    def _require_fixture_id(fixture_id: str) -> None:
        """校验账号管理 fixture 的所有权前缀。"""

        if not re.fullmatch(r"qam_[0-9a-f]{32}", fixture_id):
            raise ValueError("fixture_id must be a QuestTest account-management UUID")

    @staticmethod
    def _require_view_id(view_id: str) -> None:
        """校验读取视图 ID。"""

        if not re.fullmatch(r"read_[0-9a-f]{32}", view_id):
            raise ValueError("view_id must be a read snapshot UUID")

    def create_account(self, fixture_id: str, actor_id: int, *, exchange: str, status: str) -> int:
        """创建没有交易所凭据和 NT 映射的专用账户。"""

        self._require_fixture_id(fixture_id)
        now = datetime.now()
        with self.client.transaction() as transaction:
            result = transaction.execute(
                """INSERT INTO quant_accounts
                (exchange,email,admin_id,api_key,secret_key,api_description,status,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    exchange,
                    fixture_id + "@example.invalid",
                    actor_id,
                    "",
                    "",
                    fixture_id,
                    status,
                    now,
                    now,
                ),
            )
            if not result.lastrowid:
                raise RuntimeError("account fixture insert did not return an ID")
            account_id = int(result.lastrowid)
            transaction.execute(
                """INSERT INTO quant_account_profiles
                (account_id,name,business_owner_id,created_by,status,version,created_at,updated_at,account_code,default_risk_config)
                VALUES (%s,%s,%s,%s,%s,1,%s,%s,%s,%s)""",
                (
                    account_id,
                    fixture_id,
                    actor_id,
                    actor_id,
                    status,
                    now,
                    now,
                    fixture_id,
                    '{"enabled":false}',
                ),
            )
        return account_id

    def insert_sources(
        self,
        fixture_id: str,
        actor_id: int,
        accounts: list[dict[str, Any]],
        *,
        view_id: str,
    ) -> None:
        """写入多账号源快照和一批聚合输入，不写最终指标。"""

        self._require_fixture_id(fixture_id)
        self._require_view_id(view_id)
        if not accounts:
            raise ValueError("at least one account source is required")
        now = datetime.now(timezone.utc)
        observed_at = now.isoformat().replace("+00:00", "Z")
        batch_id = fixture_id + "_batch"
        snapshot_ids: list[str] = []
        raw_accounts: list[dict[str, Any]] = []
        raw_samples: list[dict[str, Any]] = []
        business_accounts: list[dict[str, Any]] = []
        business_profiles: list[dict[str, Any]] = []
        for row in accounts:
            account_id = int(row["account_id"])
            source_id = str(row["source_id"])
            snapshot_id = source_id + "_snapshot"
            snapshot_ids.append(snapshot_id)
            payload = {
                "currency": "USDT",
                "snapshot_time": observed_at,
                "margin_equity": row.get("margin_equity"),
                "unrealized_pnl": row.get("unrealized_pnl"),
                "realized_pnl_gross": row.get("realized_pnl"),
                "commission_expense": row.get("commission_expense"),
                "funding_pnl": row.get("funding_pnl"),
                "initial_capital": row.get("initial_capital"),
                "confirmed_deposits": row.get("confirmed_deposits", "0"),
                "confirmed_withdrawals": row.get("confirmed_withdrawals", "0"),
                "performance_source": "questtest_account_management_fixture",
            }
            payload["field_status"] = {
                key: {
                    "status": "available" if value is not None else "unavailable",
                    "reason_code": None if value is not None else "SOURCE_FIELD_UNAVAILABLE",
                    "observed_at": observed_at,
                }
                for key, value in payload.items()
                if key not in {"currency", "snapshot_time", "performance_source", "field_status"}
            }
            with self.client.transaction() as transaction:
                transaction.execute(
                    """INSERT INTO quant_account_snapshots
                    (id,created_at,updated_at,version,account_id,observed_at,source_key,source_watermark,data_status,payload)
                    VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s)""",
                    (
                        snapshot_id,
                        now.replace(tzinfo=None),
                        now.replace(tzinfo=None),
                        account_id,
                        now.replace(tzinfo=None),
                        source_id,
                        fixture_id,
                        "complete",
                        json.dumps(payload),
                    ),
                )
            raw_accounts.append(
                {
                    "AccountID": str(account_id),
                    "LocalAccountID": account_id,
                    "Exchange": row.get("exchange", "questtest_fixture"),
                    "StrategyIDs": [row["strategy_id"]] if row.get("strategy_id") else [],
                    "ActualStatus": row.get("runtime_status", "running"),
                }
            )
            business_accounts.append(
                {
                    "id": account_id,
                    "email": source_id + "@example.invalid",
                    "status": row.get("runtime_status", "running"),
                    "admin_id": actor_id,
                    "exchange": row.get("exchange", "questtest_fixture"),
                }
            )
            business_profiles.append(
                {
                    "AccountID": account_id,
                    "Name": source_id,
                    "Status": row.get("runtime_status", "running"),
                    "AccountCode": source_id,
                    "BusinessOwnerID": actor_id,
                }
            )
            for metric, source_key in (
                ("margin_equity", "account_equity_usdt"),
                ("unrealized_pnl", "account_unrealized_pnl_usdt"),
                ("realized_pnl_gross", "account_realized_pnl_usdt"),
                ("commission_expense", "account_total_commission_usdt"),
                ("funding_pnl", "account_total_funding_fee_usdt"),
            ):
                raw_samples.append(
                    {
                        "account_id": str(account_id),
                        "metric": source_key,
                        "value": row.get(metric),
                        "observed_at": observed_at,
                        "source_row_id": source_id + "_" + metric,
                    }
                )
            initial_capital = row.get("initial_capital")
            realized_pnl = row.get("realized_pnl")
            if initial_capital is not None and realized_pnl is not None:
                try:
                    capital = Decimal(str(initial_capital)) + Decimal(str(row.get("confirmed_deposits", "0"))) - Decimal(str(row.get("confirmed_withdrawals", "0")))
                    profit = Decimal(str(realized_pnl))
                    with localcontext() as context:
                        context.prec = 50
                        net_value = Decimal("1") + profit / capital if capital > 0 else None
                except (InvalidOperation, ValueError):
                    net_value = None
            else:
                net_value = None
            raw_samples.append(
                {
                    "account_id": str(account_id),
                    "metric": "account_net_value",
                    "value": str(net_value) if net_value is not None else None,
                    "observed_at": observed_at,
                    "source_row_id": source_id + "_account_net_value",
                }
            )
            for metric, source_key in (
                ("annualized_return", "account_annualized_return"),
                ("current_drawdown", "account_current_drawdown"),
            ):
                raw_samples.append(
                    {
                        "account_id": str(account_id),
                        "metric": source_key,
                        "value": row.get(metric),
                        "observed_at": observed_at,
                        "source_row_id": source_id + "_" + metric,
                    }
                )
        source = {
            "raw": {
                "fetched_at": observed_at,
                "accounts": raw_accounts,
                "samples": raw_samples,
            },
            "fixture_id": fixture_id,
        }
        current_data = {
            "account_source": source,
            "states": [],
            "positions": [],
            "position_batches": [],
            "account_positions": {str(row["account_id"]): [] for row in accounts},
        }
        manifest = {"accounts": snapshot_ids, "strategies": []}
        with self.client.transaction() as transaction:
            transaction.execute(
                """INSERT INTO quant_collection_batches
                (id,namespace,cutoff,generation,status,data_status,manifest,sources,current_data,fresh_until,expires_at,published_at,created_at,updated_at)
                VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    batch_id,
                    self.NAMESPACE,
                    now.replace(tzinfo=None),
                    "committed",
                    "complete",
                    json.dumps(manifest),
                    "[]",
                    json.dumps(current_data),
                    now.replace(tzinfo=None) + timedelta(minutes=5),
                    now.replace(tzinfo=None) + timedelta(minutes=5),
                    now.replace(tzinfo=None),
                    now.replace(tzinfo=None),
                    now.replace(tzinfo=None),
                ),
            )
            view_payload = {
                "version": 1,
                "batch_id": batch_id,
                "business": {
                    "accounts": business_accounts,
                    "profiles": business_profiles,
                    "assignments": [],
                    "definitions": [],
                    "snapshots": [],
                    "positions": [],
                    "position_batches": [],
                    "bills": [],
                    "receipts": [],
                },
                "account_snapshot_ids": snapshot_ids,
                "strategy_sample_ids": [],
            }
            context = {
                "selection_members": [str(row["account_id"]) for row in accounts],
                "minute_mode": False,
                "account_management_fixture_id": fixture_id,
                "minute_meta": {
                    "generation": observed_at,
                    "data_status": "complete",
                    "as_of": observed_at,
                },
            }
            transaction.execute(
                """INSERT INTO quant_read_views
                (id,actor_id,account_ids,context,payload,created_at,expires_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (
                    view_id,
                    actor_id,
                    json.dumps([int(row["account_id"]) for row in accounts]),
                    json.dumps(context),
                    json.dumps(view_payload),
                    now.replace(tzinfo=None),
                    now.replace(tzinfo=None) + timedelta(minutes=15),
                ),
            )

    def cleanup(
        self,
        fixture_id: str,
        actor_id: int,
        account_ids: list[int],
        source_ids: list[str],
        view_id: str,
    ) -> None:
        """按 fixture、所有者和账号集合精确清理并确认无残留。"""

        self._require_fixture_id(fixture_id)
        self._require_view_id(view_id)
        if len(account_ids) != len(source_ids):
            raise ValueError("account_ids and source_ids must have the same length")
        with self.client.transaction() as transaction:
            view = transaction.fetch_one(
                "SELECT actor_id,context FROM quant_read_views WHERE id=%s FOR UPDATE",
                (view_id,),
            )
            if view is not None and int(view["actor_id"]) != actor_id:
                raise ValueError("account management view owner mismatch")
            transaction.execute("DELETE FROM quant_read_pages WHERE view_id=%s", (view_id,))
            transaction.execute("DELETE FROM quant_read_views WHERE id=%s AND actor_id=%s", (view_id, actor_id))
            transaction.execute(
                "DELETE FROM quant_collection_batches WHERE id=%s AND namespace=%s",
                (fixture_id + "_batch", self.NAMESPACE),
            )
            for account_id, source_id in zip(account_ids, source_ids, strict=True):
                transaction.execute(
                    "DELETE FROM quant_account_snapshots WHERE account_id=%s AND source_key=%s",
                    (account_id, source_id),
                )
                transaction.execute(
                    "DELETE FROM quant_account_profiles WHERE account_id=%s AND name=%s AND created_by=%s",
                    (account_id, source_id, actor_id),
                )
                transaction.execute(
                    "DELETE FROM quant_accounts WHERE id=%s AND admin_id=%s AND api_description=%s",
                    (account_id, actor_id, source_id),
                )
        self.assert_clean(fixture_id, account_ids, source_ids, view_id)

    def assert_clean(
        self,
        fixture_id: str,
        account_ids: list[int],
        source_ids: list[str],
        view_id: str,
    ) -> None:
        """验证本次 fixture 的账号、源数据、批次和视图均为零。"""

        self._require_fixture_id(fixture_id)
        self._require_view_id(view_id)
        if len(account_ids) != len(source_ids):
            raise ValueError("account_ids and source_ids must have the same length")
        checks = (
            ("SELECT COUNT(*) AS n FROM quant_read_views WHERE id=%s", (view_id,)),
            ("SELECT COUNT(*) AS n FROM quant_read_pages WHERE view_id=%s", (view_id,)),
            ("SELECT COUNT(*) AS n FROM quant_collection_batches WHERE id=%s", (fixture_id + "_batch",)),
        )
        with self.client.transaction() as transaction:
            for query, params in checks:
                row = transaction.fetch_one(query, params)
                if row is None or int(row["n"]) != 0:
                    raise RuntimeError("account management fixture still has residual source data")
            for account_id, source_id in zip(account_ids, source_ids, strict=True):
                snapshot = transaction.fetch_one(
                    "SELECT COUNT(*) AS n FROM quant_account_snapshots WHERE account_id=%s AND source_key=%s",
                    (account_id, source_id),
                )
                if snapshot is None or int(snapshot["n"]) != 0:
                    raise RuntimeError("account management fixture still has residual snapshot data")
                row = transaction.fetch_one(
                    "SELECT COUNT(*) AS n FROM quant_accounts WHERE id=%s",
                    (account_id,),
                )
                if row is None or int(row["n"]) != 0:
                    raise RuntimeError("account management fixture still has residual account data")
                profile = transaction.fetch_one(
                    "SELECT COUNT(*) AS n FROM quant_account_profiles WHERE account_id=%s AND name=%s",
                    (account_id, source_id),
                )
                if profile is None or int(profile["n"]) != 0:
                    raise RuntimeError("account management fixture still has residual profile data")
