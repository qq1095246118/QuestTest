"""专用多账户输入视图；只组合原始输入引用，不创建计算结果。"""

from __future__ import annotations

from datetime import datetime, timedelta
import json
from typing import Any

from db.strategy_dashboard_source_fixture_repository import SourceFixtureRepository


class PortfolioFixtureRepository(SourceFixtureRepository):
    """复用单账户严格清理，只扩展同一视图中的账户集合。"""

    def insert_portfolio_view(self, fixture_id: str, actor_id: int, account_ids: list[int],
                              payload: dict[str, Any], context: dict[str, Any]) -> None:
        """创建全新 portfolio 视图；唯一键冲突报错，绝不覆盖原视图。"""
        self._require_id(fixture_id)
        now = datetime.now()
        self.client.execute(
            "INSERT INTO quant_read_views (id,actor_id,account_ids,context,payload,created_at,expires_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (fixture_id, actor_id, json.dumps(account_ids),
             json.dumps(dict(context, questtest_fixture_id=fixture_id)), json.dumps(payload),
             now, now + timedelta(minutes=5)),
        )

    def insert_ended_binding(self, fixture_id: str, account_id: int, actor_id: int,
                             assignment: dict[str, Any], definition: dict[str, Any]) -> None:
        """创建已结束的自有关系供权限/身份校验；无 NT 标识、无 active_key。"""
        self._require_id(fixture_id)
        if assignment["ID"] != fixture_id + "_run" or definition["ID"] != fixture_id + "_definition":
            raise ValueError("Binding identity is outside the fixture")
        if assignment["Status"] != "ended" or assignment.get("EndedAt") is None:
            raise ValueError("Only already-ended fixture bindings are permitted")
        now = datetime.now()
        started = datetime.fromisoformat(assignment["StartedAt"]).astimezone().replace(tzinfo=None)
        ended = datetime.fromisoformat(assignment["EndedAt"]).astimezone().replace(tzinfo=None)
        with self.client.transaction() as tx:
            tx.execute(
                "INSERT INTO quant_strategy_definitions (id,created_at,updated_at,version,code,name,researcher_id,created_by,status) "
                "VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s)",
                (definition["ID"], now, now, fixture_id, fixture_id, actor_id, actor_id, "active"),
            )
            tx.execute(
                "INSERT INTO quant_strategy_account_runs "
                "(id,created_at,updated_at,version,strategy_name,account_id,definition_id,researcher_id,business_owner_id,status,"
                "started_at,ended_at,end_reason,nt_strategy_id,nt_account_id,risk_config,runtime,active_key) "
                "VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL)",
                (assignment["ID"], now, now, fixture_id, account_id, definition["ID"], actor_id, actor_id,
                 "ended", started, ended, "questtest isolated source fixture", "", "", '{"enabled":false}',
                 json.dumps(assignment["Runtime"])),
            )

    def delete_ended_binding(self, fixture_id: str, account_id: int, actor_id: int) -> dict[str, int]:
        """核验自有已结束关系后精确删除；外部引用或身份变化会拒绝清理。"""
        self._require_id(fixture_id)
        run_id, definition_id = fixture_id + "_run", fixture_id + "_definition"
        with self.client.transaction() as tx:
            row = tx.fetch_one("SELECT account_id,definition_id,status,nt_strategy_id,nt_account_id,active_key "
                               "FROM quant_strategy_account_runs WHERE id=%s FOR UPDATE", (run_id,))
            if row and row != {"account_id": account_id, "definition_id": definition_id, "status": "ended",
                               "nt_strategy_id": "", "nt_account_id": "", "active_key": None}:
                raise ValueError("Owned ended binding changed; retained")
            other = tx.fetch_one("SELECT COUNT(*) AS n FROM quant_strategy_account_runs WHERE definition_id=%s AND id<>%s",
                                 (definition_id, run_id))
            if other and other["n"]:
                raise ValueError("Owned definition has external bindings; retained")
            run = tx.execute("DELETE FROM quant_strategy_account_runs WHERE id=%s AND account_id=%s", (run_id, account_id))
            definition = tx.execute("DELETE FROM quant_strategy_definitions WHERE id=%s AND code=%s AND created_by=%s",
                                    (definition_id, fixture_id, actor_id))
        return {"runs": run.rowcount, "definitions": definition.rowcount}

    def assert_clean(self, fixture_id: str, account_id: int) -> None:
        """重新验证账户、源数据、视图以及自有关系和定义全部为零。"""
        super().assert_clean(fixture_id, account_id)
        for table, identity in (("quant_strategy_account_runs", fixture_id + "_run"),
                                ("quant_strategy_definitions", fixture_id + "_definition")):
            row = self.client.fetch_one("SELECT COUNT(*) AS n FROM " + table + " WHERE id=%s", (identity,))
            if not row or row["n"] != 0:
                raise RuntimeError("Owned portfolio binding cleanup is incomplete")
