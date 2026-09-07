"""Backend / MCP / 测试数据库三方结果与时间语义对账。"""

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import requests
import yaml

from api.factor4_backend_api import Factor4BackendAPI
from api.factor4_summary_api import Factor4SummaryAPI
from db.factor4_read_repository import DailyReadSnapshot
from db.factor4_publication_repository import PublicationHistory
from service.factor4_read_service import Factor4ReadService, ReadCheck, ReadContractError, ReadPrecondition, compare_rows, read_tool_page, visible_daily_rows
from service.factor4_summary_service import _SUMMARY_FIELDS, summary_scope
from service.factor4_recommendation_service import Factor4RecommendationService, lifecycle_time


def _backend_items(response: requests.Response) -> tuple[dict[str, Any], ...]:
    if response.status_code != 200:
        raise ReadContractError(f"Backend read failed: HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        raise ReadContractError("Backend read is not JSON") from None
    data = body.get("data") if isinstance(body, dict) else None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or any(not isinstance(row, dict) for row in items):
        raise ReadContractError("Backend read requires data.items objects")
    return tuple(items)


def _instant(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result.astimezone(timezone.utc) if result.tzinfo is not None else None


class Factor4BackendReconciliationService:
    """使用已有测试 JWT，只读已计算结果；不能将结果对账称为原始费用重算。"""

    def __init__(self, backend: Factor4BackendAPI, read_service: Factor4ReadService) -> None:
        """保存只读两种入口；无请求或返回值。"""
        self.backend, self.read_service = backend, read_service

    def check_daily(self, snapshot: DailyReadSnapshot, label_kind: str) -> ReadCheck:
        """fact/forecast 独立日期筛选含/不含 as_of，与 MCP 和 DB 当前可见修订精确对账。"""
        rows = visible_daily_rows(snapshot, label_kind)
        if not rows:
            raise ReadPrecondition(f"BLOCKED_DATA_PRECONDITION: no current {label_kind} environment")
        target = max(rows, key=lambda row: (str(row["environment_date"]), int(row["revision"]), int(row["id"])))
        day = str(target["environment_date"])
        expected = tuple(row for row in rows if str(row["environment_date"]) == day)
        mcp = self.read_service.daily_pages(snapshot, label_kind, environment_date=day)
        baseline = self.read_service.check_daily(snapshot, label_kind, mcp, environment_date=day)
        issues = list(baseline.issues)
        fields = ("id", "environment_date", "label_kind", "label_code", "revision", "is_current")
        for as_of in (None, snapshot.as_of):
            actual = _backend_items(self.backend.daily(label_kind, environment_date=day, as_of=as_of))
            normalized = tuple({**row, "environment_date": datetime.fromisoformat(str(row["environment_date"]).replace("Z", "+00:00")).date().isoformat()} for row in actual)
            check = compare_rows(normalized, expected, fields)
            issues.extend(f"backend_daily:{issue}" for issue in check.issues)
        unfiltered = _backend_items(self.backend.daily(label_kind))
        if target["id"] not in {row.get("id") for row in unfiltered}:
            issues.append("backend_daily:latest_visible_target_missing")
        return ReadCheck(max(1, len(expected)), tuple(dict.fromkeys(issues)))

    def check_summary(self, row: dict[str, Any]) -> ReadCheck:
        """精确 completed summary 的身份/最终数值及 period 时间三方对账；无显式时区 payload 不伪造时区。"""
        backend = _backend_items(self.backend.summary_metrics(int(row["factor_id"]), bool(row["is_sub_factor_id"])))
        kind = "sub_factor" if row["is_sub_factor_id"] else "factor"
        page = read_tool_page(Factor4SummaryAPI(self.read_service.api.mcp).metrics(
            f"{kind}:{row['factor_id']}", summary_scope(row), as_of=datetime.now(timezone.utc).isoformat(), run_id=row["run_id"]))
        mcp = page.data.get("ic_summaries")
        if not isinstance(mcp, list) or any(not isinstance(item, dict) for item in mcp):
            raise ReadContractError("MCP metrics requires ic_summaries objects")
        issues: list[str] = []
        missing_period_evidence = False
        payload = row.get("metrics_json")
        payload = json.loads(payload) if isinstance(payload, str) else payload
        explicit = payload.get("summary") if isinstance(payload, dict) else None
        for label, items in (("backend", backend), ("mcp", mcp)):
            matches = [item for item in items if item.get("id") == row["id"]]
            if len(matches) != 1:
                issues.append(f"{label}:exact_metric_identity")
                continue
            # Backend serializes DECIMAL as strings and exposes a per-dimension
            # projection. Only explicit returned business fields are comparable;
            # required metric identity fields cannot be omitted.
            selected_fields = _SUMMARY_FIELDS
            normalized = matches[0]
            if label == "backend":
                identity = {"id", "run_id", "factor_id", "is_sub_factor_id", "ic_scope", "calculation_mode", "factor_bar_interval", "factor_window_bars", "return_bar_interval", "forward_return_bars", "universe_key", "symbol", "window_scope", "scoring_version"}
                selected_fields = tuple(field for field in _SUMMARY_FIELDS if field in normalized or field in identity)
                normalized = {field: Decimal(value) if isinstance(row.get(field), Decimal) and isinstance(value, str) else value for field, value in normalized.items()}
            check = compare_rows((normalized,), (row,), selected_fields)
            issues.extend(f"{label}:{issue}" for issue in check.issues)
            for field in ("period_start", "period_end"):
                expected = _instant(explicit.get(field)) if isinstance(explicit, dict) else None
                if expected is None:
                    missing_period_evidence = True
                    continue
                if _instant(matches[0].get(field)) != expected:
                    issues.append(f"{label}:period_instant={field}")
        if missing_period_evidence and not issues:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: summary values checked; payload period lacks explicit timezone")
        return ReadCheck(2, tuple(dict.fromkeys(issues)), {
            "blocked": ("summary_period_timezone_missing",) if missing_period_evidence else (),
        })

    def check_hmac_declaration(self) -> ReadCheck:
        """只验证实时 OpenAPI 对 internal/HMAC 的头与非 Bearer 声明，不把静态文档称作运行权限验证。"""
        response = self.backend.openapi()
        if response.status_code != 200:
            raise ReadContractError(f"Backend OpenAPI failed: HTTP {response.status_code}")
        try:
            spec = yaml.safe_load(response.text)
        except yaml.YAMLError:
            raise ReadContractError("Backend OpenAPI is not YAML") from None
        if not isinstance(spec, dict) or not isinstance(spec.get("paths"), dict):
            raise ReadContractError("Backend OpenAPI lacks paths")
        operations = [operation for path, methods in spec["paths"].items() if "internal" in path.casefold() or "hmac" in path.casefold()
                      for method, operation in methods.items() if method in {"get", "post", "put", "delete", "patch"} and isinstance(operation, dict)]
        if not operations:
            raise ReadPrecondition("BLOCKED_CONTRACT: no internal HMAC OpenAPI operations")
        required = {"#/components/parameters/WebhookTimestamp", "#/components/parameters/WebhookNonce", "#/components/parameters/WebhookSignature"}
        issues: list[str] = []
        for operation in operations:
            refs = {item.get("$ref") for item in operation.get("parameters", []) if isinstance(item, dict)}
            if operation.get("security") != [] or not required <= refs or "hmac" not in str(operation.get("description") or "").casefold():
                issues.append("contract:internal_hmac_declaration")
        definitions = (spec.get("components") or {}).get("parameters") or {}
        for name in ("WebhookTimestamp", "WebhookNonce", "WebhookSignature"):
            definition = definitions.get(name) or {}
            if definition.get("in") != "header" or definition.get("required") is not True:
                issues.append("contract:required_hmac_header")
        return ReadCheck(len(operations), tuple(dict.fromkeys(issues)))

    def check_recommendations(self, history: PublicationHistory, daily: DailyReadSnapshot) -> ReadCheck:
        """同一 PIT 推荐 publication/有序因子/最终分数三方对账；复用 DB route Oracle，未准备发布数据明确阻断。"""
        oracle = Factor4RecommendationService(self.read_service.api)
        baseline = oracle.check_current_routes(history, daily, limit=200)
        issues = list(baseline.issues)
        as_of = min(history.as_of, daily.as_of)
        active = [row for row in history.batches if row["is_active"]]
        for batch in active:
            args = (batch["market_scope"], batch["route_profile_key"])
            issues.extend(oracle.check_publication_at(history, *args, as_of).issues)
            mcp = read_tool_page(self.read_service.api.recommendations(*args, as_of=as_of.isoformat(), limit=200)).data
            response = self.backend.recommendations(*args, as_of=as_of)
            if response.status_code != 200:
                raise ReadContractError(f"Backend recommendation failed: HTTP {response.status_code}")
            body = response.json()
            data = body.get("data") if isinstance(body, dict) else None
            if not isinstance(data, dict):
                raise ReadContractError("Backend recommendation requires data object")
            for key in ("status", "reason_code", "returned_count"):
                if (key != "returned_count" or key in data) and data.get(key) != mcp.get(key):
                    issues.append(f"backend_recommendation:field={key}")
            for section in ("publication", "forecast"):
                actual, expected = data.get(section), mcp.get(section)
                if actual is None or expected is None:
                    if actual != expected:
                        issues.append(f"backend_recommendation:{section}_presence")
                    continue
                if not isinstance(actual, dict) or not isinstance(expected, dict):
                    issues.append(f"backend_recommendation:{section}_shape")
                    continue
                fields = ("batch_uid", "publication_uid", "market_scope", "route_profile_key", "publish_version") if section == "publication" else ("label_code", "revision", "label_status")
                for key in fields:
                    if actual.get(key) != expected.get(key):
                        issues.append(f"backend_recommendation:{section}:{key}")
                time_key = "published_at" if section == "publication" else "available_at"
                try:
                    if lifecycle_time(actual.get(time_key)) != lifecycle_time(expected.get(time_key)):
                        issues.append(f"backend_recommendation:{section}:time")
                except (TypeError, ValueError):
                    issues.append(f"backend_recommendation:{section}:invalid_time")
            actual_items, expected_items = data.get("items"), mcp.get("items")
            if not isinstance(actual_items, list) or not isinstance(expected_items, list):
                raise ReadContractError("recommendations require items list")
            keys = lambda rows: [(row.get("factor_ref"), row.get("factor_version")) for row in rows]
            if keys(actual_items) != keys(expected_items):
                issues.append("backend_recommendation:ordered_factor_membership")
            fields = ("factor_ref", "factor_type", "factor_id", "factor_version", "rank_no", "routing_score", "confidence", "time_series_score", "cross_sectional_score", "score_rule_version")
            for actual, expected in zip(actual_items, expected_items):
                normalized = {key: Decimal(value) if key in {"routing_score", "confidence", "time_series_score", "cross_sectional_score"} and isinstance(value, str) else value for key, value in actual.items()}
                issues.extend(compare_rows(({**normalized, "id": 1},), ({**expected, "id": 1},), fields).issues)
        return ReadCheck(len(active), tuple(dict.fromkeys(issues)))
