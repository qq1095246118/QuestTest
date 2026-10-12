"""策略管理数据独立对账；金额、单账号比例和聚合身份分别验证。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from api.strategy_management_api import StrategyManagementAPI, STRATEGY_PAGE_METRICS
from db.strategy_management_source_repository import StrategyManagementSourceRepository
from service.strategy_dashboard_source_statistics import daily_nav_statistics, realized_income_statistics, calmar_from_source_samples


class StrategyManagementLiveAudit:
    """执行真实页面等价查询，保留所有字段失败而非首个断言就中止。"""

    def __init__(self, api: StrategyManagementAPI, repository: StrategyManagementSourceRepository) -> None:
        """接收无缓存 API 与独立源仓储，构造没有副作用。"""
        self.api, self.repository = api, repository

    def catalog(self, from_at: str, to_at: str) -> dict[str, Any]:
        """沿同一读视图遍历目录；以账号/交易所/策略组合识别行。"""
        result: dict[str, Any] = {"requests": [], "items": [], "checks": [], "gaps": []}
        params = {"population": "all", "scope": "lifetime", "metric_basis": "accrual",
                  "metric_source": "prometheus", "metrics": STRATEGY_PAGE_METRICS,
                  "metric_from": from_at, "metric_to": to_at, "limit": 2}
        snapshot = None
        for _ in range(30):
            response = self.api.catalog(**params)
            result["requests"].append(response)
            self._equal(result, "catalog/http", 200, response["http_status"])
            if response["http_status"] != 200:
                return result
            body = response["body"]
            if snapshot is None:
                snapshot = body["meta"]["snapshot_id"]
            self._equal(result, "catalog/paging-snapshot", snapshot, body["meta"]["snapshot_id"])
            result["items"].extend(body["data"]["items"])
            if not body["data"]["has_more"]:
                break
            params.update(snapshot_id=snapshot, cursor=body["data"]["next_cursor"])
        else:
            raise RuntimeError("CATALOG_PAGINATION_NOT_COMPLETE")
        source = self.repository.source(result["requests"][0]["body"]["meta"]["fetched_at"])
        result["source_generation"] = source
        raw = source["source"]
        expected = sorted((code, account["Exchange"].lower(), account["AccountID"])
                          for account in raw["raw"]["accounts"] for code in (account.get("StrategyIDs") or []))
        actual = sorted((row["code"], row["exchange"].lower(), account)
                        for row in result["items"] for account in row["account_ids"])
        self._equal(result, "catalog/independent-full-population", expected, actual)
        for row in result["items"]:
            self.metrics(result, row["code"] + "/" + row["account_ids"][0], row["metrics"], row["account_ids"], raw)
        return result

    def overview(self, rows: list[dict[str, Any]], definition_id: str, exchange: str,
                 population: str, from_at: str, to_at: str) -> dict[str, Any]:
        """保留真实页面入口参数，验证同名跨所详情，不拿无过滤接口替代页面。"""
        result: dict[str, Any] = {"requests": [], "checks": [], "gaps": [], "definition_id": definition_id,
                                 "entry_exchange": exchange, "population": population}
        name = next(row["name"] for row in rows if row["strategy_definition_id"] == definition_id)
        selected = [row for row in rows if row["name"] == name and
                    (population == "all" or row["runtime_status"] == "running")]
        expected_members = sorted({account for row in selected for account in row["account_ids"]})
        result["expected_members"] = expected_members
        params = {"subject_type": "strategy", "subject_id": definition_id, "strategy_exchange": exchange,
                  "population": population, "scope": "cycle" if population == "running" else "lifetime",
                  "metric_basis": "display" if population == "running" else "accrual",
                  "aggregation": "linked_accounts", "metric_source": "prometheus",
                  "from": from_at, "to": to_at, "metrics": STRATEGY_PAGE_METRICS}
        response = self.api.analytics("summary", **params)
        result["requests"].append(response)
        self._equal(result, "summary/http", 200, response["http_status"])
        if response["http_status"] != 200:
            return result
        body = response["body"]
        self._equal(result, "summary/same-name-cross-exchange-members", expected_members,
                    sorted(body["data"]["context"].get("source_account_ids", [])))
        source = self.repository.source(body["meta"]["fetched_at"])
        result["source_generation"] = source
        self.metrics(result, "summary", body["data"]["metrics"], expected_members, source["source"])
        exposure = body["data"].get("exposure_summary", {})
        if all(exposure.get(field, {}).get("value") is not None for field in
               ("long_notional", "short_notional", "net_exposure_amount", "net_exposure_rate", "margin_equity")):
            value = lambda field: Decimal(exposure[field]["value"])
            self._equal(result, "net_exposure/long-minus-short", value("long_notional") - value("short_notional"), value("net_exposure_amount"))
            self._equal(result, "net_exposure/amount-rate-consistency", value("net_exposure_rate") * value("margin_equity"), value("net_exposure_amount"), tolerance="0.0001")
        return result

    def metrics(self, result: dict[str, Any], prefix: str, metrics: dict[str, Any],
                members: list[str], source: dict[str, Any]) -> None:
        """逐字段核对独立样本；缺源或组合契约不明明确登记，不算数值通过。"""
        amounts = {"margin_equity": "account_equity_usdt", "unrealized_pnl": "account_unrealized_pnl_usdt",
                   "realized_pnl_gross": "account_realized_pnl_usdt", "commission_expense": "account_total_commission_usdt",
                   "funding_pnl": "account_total_funding_fee_usdt"}
        ratios = {"annualized_return": "account_annualized_return", "current_drawdown": "account_current_drawdown",
                  "max_drawdown": "account_max_drawdown", "sharpe_ratio": "account_sharpe_ratio", "cumulative_return": "account_net_value"}
        samples = source["raw"]["samples"]
        for field, metric in metrics.items():
            if metric.get("value") is None:
                self._equal(result, prefix + "/" + field + "/null-has-reason", True, bool(metric.get("reason_code")))
            if field not in amounts and field not in ratios:
                continue
            selected = [sample for sample in samples if sample.get("account_id") in members
                        and sample["metric"] == (amounts | ratios)[field]]
            if not members or len(selected) != len(members):
                result["gaps"].append({"field": field, "members": members, "reason": "RAW_SAMPLE_MISSING", "prefix": prefix})
                continue
            if field in ratios and len(members) > 1:
                result["gaps"].append({"field": field, "members": members, "reason": "PORTFOLIO_RULE_APPROVAL_REQUIRED", "prefix": prefix})
                continue
            expected = sum((Decimal(str(sample["value"])) for sample in selected), Decimal(0))
            if field == "cumulative_return":
                expected -= 1
            self._equal(result, prefix + "/source/" + field, expected, metric.get("value"))
        if len(members) == 1:
            aid = members[0]
            statistics = [stat for stat in source.get("statistics", []) if stat["account_id"] == aid]
            for field in ("volatility", "sortino_ratio", "win_rate", "profit_loss_ratio"):
                if field not in metrics:
                    continue
                stats = next((stat for stat in statistics if stat["field"] ==
                    ("sortino_ratio" if field in {"volatility", "sortino_ratio"} else "win_rate")), None)
                try:
                    expected = (daily_nav_statistics(stats["nav"]) if field in {"volatility", "sortino_ratio"}
                                else realized_income_statistics(stats["trades"]))[field]
                except (KeyError, ValueError, TypeError):
                    result["gaps"].append({"field": field, "members": members, "reason": "RAW_STATISTIC_UNAVAILABLE", "prefix": prefix})
                else:
                    self._equal(result, prefix + "/recomputed/" + field, expected, metrics[field]["value"])
            if "calmar_ratio" in metrics:
                try:
                    expected = calmar_from_source_samples(samples, aid)["calmar_ratio"]
                except ValueError:
                    result["gaps"].append({"field": "calmar_ratio", "reason": "RAW_STATISTIC_UNAVAILABLE", "prefix": prefix})
                else:
                    self._equal(result, prefix + "/recomputed/calmar_ratio", expected, metrics["calmar_ratio"]["value"], tolerance="0.000001")

    @staticmethod
    def _equal(result: dict[str, Any], name: str, expected: Any, actual: Any,
               tolerance: str = "0.000001") -> None:
        """保留独立数值预期、实际和字段名，不用接口输出改写金值。"""
        passed = expected == actual
        if expected is not None and actual is not None and not isinstance(expected, bool):
            try:
                passed = abs(Decimal(str(expected)) - Decimal(str(actual))) <= Decimal(tolerance)
            except (ArithmeticError, ValueError, TypeError):
                pass
        result["checks"].append({"name": name, "status": "PASS" if passed else "FAIL", "expected": expected, "actual": actual})
