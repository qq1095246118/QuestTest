"""汇总指标和 factor_rank 的真实业务核验；与环境 route 排名分开。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo
import json

from api.factor4_summary_api import Factor4SummaryAPI, RankingMode, SummaryScope
from db.factor4_read_repository import MetricScopeSnapshot, SUMMARY_KEYS, SummarySample, ValiditySample, SliceSample
from service.factor4_read_service import (
    ReadCheck, ReadContractError, ReadPrecondition, ToolPage, compare_rows,
    read_tool_body, read_tool_page,
)

_LOCAL = ZoneInfo("Asia/Shanghai")
_PERIOD_FIELDS = (
    "period_start", "period_end", "is_period_start", "is_period_end",
    "oos_period_start", "oos_period_end",
)
_SUMMARY_FIELDS = (
    "id", "run_id", "factor_id", "is_sub_factor_id", *SUMMARY_KEYS,
    "interval_value", "forward_return_horizon", "metric_window_bars", "metric_window_days",
    "slice_count", "valid_slice_count", "coverage_mean", "coverage_min", "mean_ic", "median_ic",
    "std_ic", "icir", "mean_abs_ic", "positive_ic_rate", "mean_rank_ic", "median_rank_ic",
    "std_rank_ic", "rank_icir", "mean_abs_rank_ic", "positive_rank_ic_rate", "ic_t_stat",
    "rank_ic_t_stat", "monotonicity_ratio", "mean_long_short_return", "long_short_annual_return",
    "long_short_t_stat", "is_slice_count", "oos_slice_count", "is_icir", "oos_icir",
    "icir_oos_retention", "rank_is_icir", "rank_oos_icir", "rank_icir_oos_retention",
    "ic_score", "rank_ic_score", "icir_score", "rank_icir_score", "t_stat_score",
    "oos_retention_score", "monotonicity_score", "long_short_score", "final_score",
)


def summary_scope(row: Mapping[str, Any]) -> SummaryScope:
    """将 DB 维度转换为完整端点参数；保留数据库字段类型，缺字段 KeyError，无 I/O。"""
    return SummaryScope(**{key: row[key] for key in SUMMARY_KEYS if key != "factor_bar_interval"},
                        interval=row["factor_bar_interval"])


def metric_slice_arguments(sample: SliceSample, *, limit: int = 7, explicit_run: bool = True,
                           as_of: str | None = None) -> dict[str, Any]:
    """Build a bounded exact-scope slice read; missing natural rows raise ReadPrecondition.

    The final persisted slice end is advanced one day to exclude the deferred equality
    boundary. Invalid DB timestamps/required fields raise ValueError/KeyError; no I/O.
    """
    summary, rows = sample.summary, sample.rows
    if not rows:
        raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no persisted metric slices")
    start = min(_time(row["slice_start"], timezone.utc) for row in rows)
    end = max(_time(row["slice_end"], timezone.utc) for row in rows)
    captured = _time(as_of) if as_of is not None else datetime.now(timezone.utc)
    upper = min(end + timedelta(days=1), captured)
    if upper <= end:
        raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no visible instant after the final slice end")
    args = {"factor_ref": f"{'sub_factor' if summary.get('is_sub_factor_id', 1) else 'factor'}:{summary['factor_id']}",
            **{key: value for key, value in summary.items() if key in SUMMARY_KEYS and key != "factor_bar_interval"},
            "interval": summary["factor_bar_interval"], "symbol": summary.get("symbol") or "",
            "as_of": captured.isoformat(), "start_time": start.isoformat(),
            "end_time": upper.isoformat(), "limit": min(max(int(limit), 1), 100)}
    if explicit_run:
        args["run_id"] = summary["run_id"]
    return args


def validity_arguments(sample: ValiditySample, scope: str, *, as_of: str | None = None,
                        explicit_run: bool = False) -> dict[str, Any]:
    """Build one TS/CS validity request from DB identities; invalid scope raises ValueError.

    A missing FK can still be queried to test visibility suppression. No summary fields
    are manufactured as an oracle: only request defaults use direct/aggregate semantics.
    Missing required DB identity fields raise KeyError; this function performs no I/O.
    """
    if scope not in {"ts", "cs"}:
        raise ValueError("scope must be ts or cs")
    row, summary = sample.validity, sample.summaries.get(scope, {})
    name = "time_series" if scope == "ts" else "cross_sectional"
    args = {"factor_ref": f"{'sub_factor' if row.get('is_sub_factor_id', 1) else 'factor'}:{row['factor_id']}",
            "validity_scope": name, "calculation_mode": summary.get("calculation_mode") or "direct",
            "universe_key": row["universe_key"], "window_scope": row["window_scope"],
            "interval": row["factor_bar_interval"], "factor_window_bars": row["factor_window_bars"],
            "return_bar_interval": row["return_bar_interval"], "forward_return_bars": int(row["forward_return_bars"]),
            "scoring_version": row.get(f"{name}_scoring_version") or summary.get("scoring_version"),
            "symbol": summary.get("symbol") or "", "as_of": as_of or datetime.now(timezone.utc).isoformat()}
    if explicit_run:
        args["run_id"] = row["run_id"]
    return args


def compare_validity(item: Mapping[str, Any], sample: ValiditySample, scope: str) -> ReadCheck:
    """Check the public validity identity, selected summary FK, status, flag and score.

    Inputs are a successful public item and persisted oracle. Missing public fields
    are mismatches, not silently ignored. Invalid scope raises ValueError; no I/O.
    """
    if scope not in {"ts", "cs"}:
        raise ValueError("scope must be ts or cs")
    row, summary = sample.validity, sample.summaries[scope]
    key = "time_series" if scope == "ts" else "cross_sectional"
    expected = {"id": row.get("id"), "metric_id": summary.get("id"), "run_id": row.get("run_id"),
                "factor_id": row.get("factor_id"), "validity_status": row.get(f"{key}_status"),
                f"{key}_is_valid": row.get(f"{key}_is_valid"), f"{key}_score": row.get(f"{key}_score")}
    issues = []
    for field, value in expected.items():
        if field not in item:
            issues.append(f"validity:missing_{field}")
        elif item[field] is None or value is None:
            if item[field] is not value:
                issues.append(f"validity:{field}")
        elif isinstance(value, (int, float, Decimal)):
            if not _equal_number(int(item[field]) if isinstance(item[field], bool) else item[field], value):
                issues.append(f"validity:{field}")
        elif item[field] != value:
            issues.append(f"validity:{field}")
    for field in ("overall_is_valid", "overall_status", f"{key}_status"):
        if field in item and field in row:
            actual = int(item[field]) if isinstance(item[field], bool) else item[field]
            if str(actual) != str(row[field]):
                issues.append(f"validity:{field}")
    return ReadCheck(1, tuple(issues))


def _time(value: Any, zone: Any = None) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        if zone is None:
            raise ValueError("timestamp has no timezone")
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(timezone.utc)


def _number(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError("not a numeric metric")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("metric must be finite")
    return result


def _equal_number(left: Any, right: Any) -> bool:
    try:
        return abs(_number(left) - _number(right)) <= Decimal("1e-11")
    except (ValueError, ArithmeticError):
        return False


def _identity(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(key) for key in SUMMARY_KEYS)


def visible_metric_scopes(snapshot: MetricScopeSnapshot, as_of: datetime) -> dict[tuple[Any, ...], dict[str, Any]]:
    """从所有已完成记录独立重建 as-of scope：因子并集、最大完成时点与最大周期。

    输入固定 DB 快照和 aware as-of；返回按 scope 索引的预期，非法时间 ValueError。
    绝不将各 Run 的 distinct count 相加，不依赖 API 返回的计数。
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in snapshot.rows:
        if _time(row["run_completed_at"], _LOCAL) <= as_of:
            groups[_identity(row)].append(row)
    result: dict[tuple[Any, ...], dict[str, Any]] = {}
    for key, rows in groups.items():
        periods = [_time(row["metric_period_end"], timezone.utc) for row in rows if row["metric_period_end"] is not None]
        result[key] = {**dict(zip(SUMMARY_KEYS, key)), "kind": snapshot.kind,
                       "available_factor_count": len({row["factor_id"] for row in rows}),
                       "run_completed_at": max(_time(row["run_completed_at"], _LOCAL) for row in rows),
                       "metric_period_end": max(periods, default=None)}
    return result


def compare_summary(actual: Sequence[Mapping[str, Any]], expected: Sequence[Mapping[str, Any]]) -> ReadCheck:
    """按主键与全部公开指标字段对账（包括 null）；周期按 UTC，错误返回字段不返回值。"""
    check = compare_rows(actual, expected, _SUMMARY_FIELDS)
    issues = list(check.issues)
    for row in expected:
        issues.extend(f"summary:db_missing={key}" for key in _SUMMARY_FIELDS if key not in row)
    expected_by_id = {row["id"]: row for row in expected}
    for row in actual:
        oracle = expected_by_id.get(row.get("id"))
        if oracle is None:
            continue
        for key in _PERIOD_FIELDS:
            try:
                if key not in row or key not in oracle:
                    issues.append(f"summary:missing_{key}")
                    continue
                av = _time(row[key]) if row[key] is not None else None
                ev = _time(oracle[key], timezone.utc) if oracle[key] is not None else None
                if av != ev:
                    issues.append(f"summary:field={key}")
            except (ValueError, TypeError):
                issues.append(f"summary:invalid_{key}")
    return ReadCheck(len(expected), tuple(issues))


def compare_rank(
    page: ToolPage, sample: SummarySample, *, ranking_mode: RankingMode,
    metric: str, top_k: int, bottom_k: int,
) -> ReadCheck:
    """Compare the complete DB candidate set with public ranking values and selections.

    Signed ranking accepts only explicit persisted direction_sign evidence, never a
    response direction or an inferred training-IC sign. Missing direction evidence
    blocks only when no independently detectable mismatch exists. Unresolved signed
    candidates may be excluded, so raw candidate counts only bound result size until
    directions are complete. Ties have no invented identity tie-breaker or refill policy.
    Missing samples raise ReadPrecondition; malformed arrays raise ReadContractError.
    """
    if not sample.rows:
        raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no summary rank candidates")
    top, bottom = page.data.get("top_items"), page.data.get("bottom_items")
    if not isinstance(top, list) or not isinstance(bottom, list) or any(not isinstance(r, dict) for r in top + bottom):
        raise ReadContractError("rank top_items/bottom_items must be object arrays")
    issues: list[str] = []
    by_factor = {row["factor_id"]: row for row in sample.rows}
    directions = {row["factor_id"]: _persisted_rank_direction(row) for row in sample.rows} if ranking_mode == "signed" else {}
    raw_candidates: dict[Any, Decimal] = {}
    for row in sample.rows:
        try:
            raw_candidates[row["factor_id"]] = _number(row.get(metric))
        except (ValueError, ArithmeticError):
            continue
    missing_direction = ranking_mode == "signed" and any(directions.get(fid) is None for fid in raw_candidates)
    all_items = top + bottom
    if page.data.get("returned_count") != len(all_items):
        issues.append("rank:returned_count")
    if page.data.get("validity_evaluated") is not False:
        issues.append("rank:unexpected_validity_filter")
    seen_ids, seen_refs = set(), set()
    for item in all_items:
        mid, ref = item.get("metric_id"), item.get("factor_ref")
        if not isinstance(mid, int) or isinstance(mid, bool) or not isinstance(ref, str):
            issues.append("rank:invalid_identity_type")
            continue
        if mid in seen_ids or ref in seen_refs:
            issues.append("rank:duplicate_item")
        seen_ids.add(mid)
        seen_refs.add(ref)
        row = by_factor.get(item.get("factor_id"))
        if row is None:
            issues.append("rank:unknown_factor")
            continue
        if mid != row["id"] or item.get("run_id") != row["run_id"]:
            issues.append("rank:not_latest_metric_run")
        if ref != f"{sample.kind}:{row['factor_id']}" or item.get("kind") != sample.kind:
            issues.append("rank:factor_identity")
        for key in SUMMARY_KEYS:
            if key not in item or item[key] != sample.scope[key]:
                issues.append(f"rank:scope={key}")
        if not _equal_number(item.get("raw_metric_value"), row.get(metric)):
            issues.append("rank:raw_metric_value")
        try:
            raw = _number(row.get(metric))
            if ranking_mode == "signed":
                sign = _number(item.get("direction_sign"))
                if sign not in {-1, 1}:
                    issues.append("rank:direction_sign")
                persisted_sign = directions.get(row["factor_id"])
                if persisted_sign is None:
                    _number(item.get("ranking_value"))
                    continue
                if sign != persisted_sign:
                    issues.append("rank:direction_sign")
                expected = raw * persisted_sign
            else:
                expected = abs(raw) if ranking_mode == "absolute_diagnostic" else raw
            if not _equal_number(item.get("ranking_value"), expected):
                issues.append("rank:ranking_value")
        except (ValueError, ArithmeticError):
            issues.append("rank:nonfinite_or_missing_value")
    candidates = [abs(value) if ranking_mode == "absolute_diagnostic"
                  else value * directions[fid] if ranking_mode == "signed" else value
                  for fid, value in raw_candidates.items()
                  if ranking_mode != "signed" or directions.get(fid) is not None]
    if not raw_candidates and (top_k or bottom_k):
        raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: selected metric has no finite candidates")
    ambiguous_overlap = False
    if top_k and bottom_k and candidates and not missing_direction:
        sorted_values = sorted(candidates, reverse=True)
        top_cutoff = sorted_values[min(top_k, len(sorted_values)) - 1]
        bottom_cutoff = sorted(candidates)[min(bottom_k, len(candidates)) - 1]
        ambiguous_overlap = top_cutoff <= bottom_cutoff
    for name, items, limit, descending in (("top", top, top_k, True), ("bottom", bottom, bottom_k, False)):
        expected_size = min(limit, len(raw_candidates))
        if (len(items) > expected_size or (descending and not missing_direction and len(items) != expected_size)
                or (not descending and not ambiguous_overlap and not missing_direction and len(items) != expected_size)):
            issues.append(f"rank:{name}_cardinality")
        try:
            values = [_number(item.get("ranking_value")) for item in items]
            if values != sorted(values, reverse=descending):
                issues.append(f"rank:{name}_order")
            if not missing_direction:
                # No tie-breaker is invented: compare ordered values and DB-backed identity membership.
                expected_values = sorted(candidates, reverse=descending)[:limit]
                # The tie-breaker and refill policy are not contracted. With an overlapping
                # cutoff, validate extreme values/identity/order only; never invent a refill.
                selection_bad = any(not _equal_number(a, b) for a, b in zip(values, expected_values))
                if not (ambiguous_overlap and not descending):
                    selection_bad = selection_bad or len(values) != len(expected_values)
                if selection_bad:
                    issues.append(f"rank:{name}_selection")
        except (ValueError, ArithmeticError):
            issues.append(f"rank:{name}_invalid_values")
    if missing_direction and not issues:
        raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: signed ranking lacks explicit persisted direction_sign for the complete candidate set")
    if ambiguous_overlap and not issues:
        raise ReadPrecondition("BLOCKED_DOC: overlapping Top/Bottom cutoffs need a documented exclusion/refill policy before complete selection can be verified")
    return ReadCheck(len(sample.rows), tuple(dict.fromkeys(issues)),
                     {"candidates": len(raw_candidates), "returned": len(all_items),
                      "tie_overlap_selection_limited": ambiguous_overlap,
                      "direction_oracle_complete": not missing_direction})


def _persisted_rank_direction(row: Mapping[str, Any]) -> Decimal | None:
    """Read explicit direction_sign from a DB summary or its persisted JSON summary.

    Only named scalar direction fields are supported. Missing, conflicting or invalid
    evidence returns None; neither formula syntax nor training statistics imply a sign.
    """
    containers: list[Mapping[str, Any]] = [row]
    payload = row.get("metrics_json")
    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return None
    if isinstance(payload, Mapping):
        containers.append(payload)
        if isinstance(payload.get("summary"), Mapping):
            containers.append(payload["summary"])
    signs: list[Decimal] = []
    for container in containers:
        if "direction_sign" not in container:
            continue
        try:
            sign = _number(container["direction_sign"])
        except (ValueError, ArithmeticError):
            return None
        if sign not in {-1, 1}:
            return None
        signs.append(sign)
    return signs[0] if signs and len(set(signs)) == 1 else None


class Factor4SummaryService:
    """以数据库最终结果为 Oracle；不启动计算，不读旧报告，不调用 tmp。"""

    def __init__(self, api: Factor4SummaryAPI) -> None:
        """输入语义 API，保存引用；无 I/O、无业务异常。"""
        self.api = api

    def check_metrics(self, sample: SummarySample, *, explicit_run: bool = False, batch: bool = False) -> ReadCheck:
        """逐一或批量读取小分区所有因子，逐字段核对最新汇总；结构/依赖异常透传。"""
        if not sample.rows:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no summary rows")
        scope = summary_scope(sample.scope)
        issues: list[str] = []
        refs = tuple(f"{sample.kind}:{row['factor_id']}" for row in sample.rows)
        payloads = []
        if batch:
            page = read_tool_page(self.api.metrics_batch(refs, scope, as_of=sample.as_of.isoformat()))
            items = page.items
            if len(items) != len(refs) or {item.get("factor_ref") for item in items} != set(refs):
                issues.append("summary:batch_membership")
            for item in items:
                if item.get("success") is not True or not isinstance(item.get("data"), dict):
                    issues.append("summary:batch_item_failed")
                    continue
                # Batch returns one flattened summary in item.data; single returns ic_summaries.
                payloads.append((item["factor_ref"], {"factor_ref": item["data"].get("factor_ref"),
                                                     "ic_summaries": [item["data"]]}))
        else:
            for ref, row in zip(refs, sample.rows):
                page = read_tool_page(self.api.metrics(ref, scope, as_of=sample.as_of.isoformat(),
                                                      run_id=row["run_id"] if explicit_run else None))
                resolved = page.data.get("resolved_scope")
                for key in ("ic_scope", "calculation_mode", "factor_window_bars", "return_bar_interval",
                            "forward_return_bars", "universe_key", "symbol", "window_scope", "scoring_version"):
                    if not isinstance(resolved, dict) or key not in resolved:
                        issues.append(f"summary:resolved_scope_missing={key}")
                    elif str(resolved[key] or "") != str(sample.scope.get(key) or ""):
                        issues.append(f"summary:resolved_scope={key}")
                payloads.append((ref, page.data))
        by_ref = dict(zip(refs, sample.rows))
        for ref, payload in payloads:
            if payload.get("factor_ref") != ref or ref not in by_ref:
                issues.append("summary:factor_identity")
                continue
            rows = payload.get("ic_summaries")
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ReadContractError("ic_summaries must be an object array")
            issues.extend(compare_summary(rows, [by_ref[ref]]).issues)
        return ReadCheck(len(sample.rows), tuple(issues))

    def check_concurrent_metrics_repeatability(self, sample: SummarySample) -> ReadCheck:
        """Run three exact metric reads concurrently and compare each with the same frozen DB row.

        This is a separately deferred concurrency contract, not a performance claim.
        Missing natural data raises ReadPrecondition; transport/protocol errors from
        every worker propagate, and no worker failure is converted to an empty result.
        """
        if not sample.rows:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no exact metric concurrency fixture")
        row = sample.rows[0]
        ref = f"{sample.kind}:{row['factor_id']}"
        def fetch() -> ToolPage:
            return read_tool_page(self.api.metrics(ref, summary_scope(sample.scope),
                as_of=sample.as_of.isoformat(), run_id=row["run_id"]))
        with ThreadPoolExecutor(max_workers=3) as workers:
            pages = tuple(workers.map(lambda _: fetch(), range(3)))
        issues = []
        payloads = []
        for page in pages:
            summaries = page.data.get("ic_summaries")
            if not isinstance(summaries, list) or any(not isinstance(item, dict) for item in summaries):
                raise ReadContractError("concurrent metric result requires ic_summaries")
            issues.extend(compare_summary(summaries, [row]).issues)
            payloads.append(summaries)
        if any(payload != payloads[0] for payload in payloads[1:]):
            issues.append("summary:concurrent_exact_read_changed")
        return ReadCheck(3, tuple(issues))

    def check_rank(
        self, sample: SummarySample, *, ranking_mode: RankingMode = "signed", metric: str = "mean_ic",
        top_k: int = 2, bottom_k: int = 1, repeat: bool = False,
    ) -> ReadCheck:
        """执行真实 factor_rank 并对账，可验证固定 as-of 重放；错误/缺前置安全透传。"""
        def fetch() -> ToolPage:
            return read_tool_page(self.api.rank(summary_scope(sample.scope), kind=sample.kind,
                as_of=sample.as_of.isoformat(), metric=metric, ranking_mode=ranking_mode,
                top_k=top_k, bottom_k=bottom_k))
        page = fetch()
        check = compare_rank(page, sample, ranking_mode=ranking_mode, metric=metric, top_k=top_k, bottom_k=bottom_k)
        issues = list(check.issues)
        if repeat:
            replay = fetch()
            issues.extend(compare_rank(replay, sample, ranking_mode=ranking_mode, metric=metric,
                                       top_k=top_k, bottom_k=bottom_k).issues)
            if page.data != replay.data:
                issues.append("rank:replay_changed")
        return ReadCheck(check.checked_count, tuple(issues), check.evidence)

    def check_scopes(
        self, snapshot: MetricScopeSnapshot, *, limit: int = 100, offset: int | None = None,
    ) -> ReadCheck:
        """对账 scope 并集/生命周期/周期和有界页；PIT 必須真正观察到目标，不能因截断缺失而 PASS。"""
        as_of = snapshot.as_of
        target = None
        if offset is not None:
            groups: dict[tuple[Any, ...], set[datetime]] = defaultdict(set)
            for row in snapshot.rows:
                time = _time(row["run_completed_at"], _LOCAL)
                if time <= as_of:
                    groups[_identity(row)].add(time)
            candidates = [(key, max(times)) for key, times in groups.items() if len(times) >= 2]
            if not candidates:
                raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no scope with distinct completed run times")
            target, boundary = max(candidates, key=lambda pair: pair[1])
            as_of = boundary + timedelta(microseconds=offset)
        expected = visible_metric_scopes(snapshot, as_of)
        if not expected:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no visible metric scopes")
        page = read_tool_page(self.api.list_scopes(kind=snapshot.kind, **snapshot.filters,
                                                  as_of=as_of.isoformat(), limit=limit))
        issues: list[str] = []
        seen = set()
        for row in page.items:
            identity = _identity(row)
            if identity in seen:
                issues.append("scopes:duplicate_identity")
            seen.add(identity)
            oracle = expected.get(identity)
            if oracle is None:
                issues.append("scopes:unexpected_or_future_scope")
                continue
            for key in ("kind", "available_factor_count", *SUMMARY_KEYS):
                if key not in row or row[key] != oracle[key]:
                    issues.append(f"scopes:field={key}")
            for key in ("run_completed_at", "metric_period_end"):
                try:
                    actual = _time(row[key]) if row.get(key) is not None else None
                    if key not in row or actual != oracle[key]:
                        issues.append(f"scopes:field={key}")
                except (ValueError, TypeError):
                    issues.append(f"scopes:invalid={key}")
        if len(page.items) != min(len(expected), limit):
            issues.append("scopes:page_cardinality")
        if len(expected) <= limit and seen != set(expected):
            issues.append("scopes:missing_identity")
        if page.meta.get("next_cursor") or page.meta.get("truncated") != (len(expected) > limit):
            issues.append("scopes:pagination_contract")
        if target is not None and target not in seen and len(expected) > limit and not issues:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: PIT target outside capped scope page")
        return ReadCheck(len(page.items) or 1, tuple(issues), {"expected_scopes": len(expected), "returned_scopes": len(page.items)})

    def check_formula_completion(self, sample: SummarySample, offset: int) -> ReadCheck:
        """完成时点前/等/后读取不可变公式，核对 Run、hash、表达式与周期；无合适证据明确阻断。"""
        selected = None
        for row in sample.rows:
            matches = [f for f in sample.formulas if f["factor_id"] == row["factor_id"] and f["run_id"] == row["run_id"]]
            if len(matches) == 1 and _time(matches[0]["recorded_at"], _LOCAL) <= _time(row["run_completed_at"], _LOCAL):
                selected = row, matches[0]
                break
        if selected is None:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no unique formula recorded before run completion")
        row, evidence = selected
        as_of = _time(row["run_completed_at"], _LOCAL) + timedelta(microseconds=offset)
        ref = f"{sample.kind}:{row['factor_id']}"
        response = self.api.mcp.get_formula(ref, run_id=row["run_id"],
            interval=row["factor_bar_interval"], factor_window_bars=row["factor_window_bars"],
            return_bar_interval=row["return_bar_interval"], forward_return_bars=row["forward_return_bars"],
            calculation_mode=row["calculation_mode"], as_of=as_of.isoformat())
        body = read_tool_body(response)
        if offset < 0:
            code = (body.get("error") or {}).get("code")
            if response.is_tool_error and code == "FORMULA_EVIDENCE_NOT_FOUND":
                return ReadCheck(1)
            if response.is_tool_error:
                read_tool_page(response)  # Dependency errors must remain blocked, not count as invisibility.
            return ReadCheck(1, ("formula:visible_before_completion",))
        data = read_tool_page(response).data
        issues = []
        for key in ("run_id", "formula_hash", "formula_version"):
            if key not in data or data[key] != evidence[key]:
                issues.append(f"formula:field={key}")
        if data.get("factor_ref") != ref:
            issues.append("formula:factor_identity")
        if data.get("expression") != evidence["expression"]:
            issues.append("formula:expression")
        identity = data.get("metric_identity")
        for key in ("calculation_mode", "factor_bar_interval", "factor_window_bars",
                    "return_bar_interval", "forward_return_bars"):
            if not isinstance(identity, dict) or identity.get(key) != row[key]:
                issues.append(f"formula:scope={key}")
        if data.get("bar_interval") != row["factor_bar_interval"]:
            issues.append("formula:bar_interval")
        return ReadCheck(1, tuple(issues))

    def check_validity(self, sample: ValiditySample, scope: str, *, as_of: str | None = None, explicit_run: bool = False) -> ReadCheck:
        """Compare MCP validity item with the selected persisted DB validity row and summary FK."""
        args = validity_arguments(sample, scope, as_of=as_of, explicit_run=explicit_run)
        page = read_tool_page(self.api.validity(args)); item = page.data.get("item")
        if not isinstance(item, dict): raise ReadContractError("validity item must be an object")
        return compare_validity(item, sample, scope)

    def check_metric_slices(self, sample: SliceSample, *, limit: int = 500, explicit_run: bool = True,
                            as_of: str | None = None, resolved_scope: bool = False) -> ReadCheck:
        """Compare all cursor-paginated slice identities/values with DB rows for an exact summary.

        The endpoint may emit a very large text representation for high limits. Pages are
        deliberately bounded to 100 and followed by their signed cursor, so a truncated
        response cannot be mistaken for a complete reconciliation.
        """
        summary, expected = sample.summary, sample.rows
        if not expected: raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no persisted metric slices")
        args = metric_slice_arguments(sample, limit=limit, explicit_run=explicit_run, as_of=as_of)
        requested = args["limit"]
        actual_rows: list[dict[str, Any]] = []
        pages = 0
        seen_cursors: set[str] = set()
        issues: list[str] = []
        while True:
            page = read_tool_page(self.api.metric_slices(args))
            if len(page.items) > requested:
                issues.append("slices:page_limit")
            if "returned_count" in page.data and page.data["returned_count"] != len(page.items):
                issues.append("slices:returned_count")
            if resolved_scope:
                actual_scope = page.data.get("resolved_scope")
                for key in (*[key for key in SUMMARY_KEYS if key != "factor_bar_interval"], "interval"):
                    if not isinstance(actual_scope, dict) or key not in actual_scope:
                        issues.append(f"slices:resolved_scope_missing={key}")
                    elif str(actual_scope[key]) != str(args[key]):
                        issues.append(f"slices:resolved_scope={key}")
            actual_rows.extend(page.items)
            pages += 1
            cursor = page.meta.get("next_cursor")
            if not cursor:
                break
            if not isinstance(cursor, str) or cursor in seen_cursors or not page.items or pages >= 50:
                raise ReadContractError("slices cursor did not advance within safety bound")
            seen_cursors.add(cursor)
            args["cursor"] = cursor
        actual = actual_rows
        if len(actual) > len(expected): issues.append("slices:returned_more_than_db")
        exp_ids={r.get("id") for r in expected}; act_ids={r.get("id") for r in actual}
        if exp_ids != act_ids: issues.append("slices:membership")
        if len(actual) != len(act_ids): issues.append("slices:duplicate_identity")
        if [r.get("id") for r in actual] != [r.get("id") for r in expected]:
            issues.append("slices:order_or_membership")
        for row in actual:
            if row.get("id") in exp_ids:
                oracle=next(r for r in expected if r.get("id")==row.get("id"))
                for key in ("run_id", "factor_id", "is_sub_factor_id", "ic_scope", "calculation_mode",
                            "factor_bar_interval", "factor_window_bars", "return_bar_interval", "forward_return_bars",
                            "universe_key", "window_scope", "symbol", "slice_start", "slice_end", "as_of_time",
                            "interval_value", "forward_return_horizon", "metric_window_bars", "metric_window_days",
                            "sample_count", "sample_segment", "ic", "rank_ic", "icir", "rank_icir", "ic_abs", "rank_ic_abs", "coverage",
                            "ic_p_value", "rank_ic_p_value", "ic_t_stat", "rank_ic_t_stat", "slice_score",
                            "top_quantile_return", "bottom_quantile_return", "long_short_return", "long_short_annual_return",
                            "ic_score", "rank_ic_score", "icir_score", "rank_icir_score", "t_stat_score", "monotonicity_score", "long_short_score", "created_at", "monotonicity_ratio", "stratification"):
                    if key not in oracle:
                        continue
                    if key not in row:
                        issues.append(f"slices:missing_{key}")
                        continue
                    left, right = row[key], oracle[key]
                    if left is None or right is None:
                        mismatch = left is not right
                    elif key in {"slice_start", "slice_end", "as_of_time", "created_at"}:
                        try:
                            mismatch = _time(left) != _time(right, _LOCAL if key == "created_at" else timezone.utc)
                        except (ValueError, TypeError):
                            mismatch = True
                    elif isinstance(right, (int, float, Decimal)):
                        mismatch = not _equal_number(left, right)
                    elif key == "stratification":
                        try:
                            mismatch = (json.loads(left) if isinstance(left, str) else left) != (json.loads(right) if isinstance(right, str) else right)
                        except (TypeError, ValueError):
                            mismatch = True
                    else:
                        mismatch = str(left) != str(right)
                    if mismatch: issues.append(f"slices:{key}")
        return ReadCheck(len(expected), tuple(sorted(set(issues))))

    def check_validity_batch(self, sample: ValiditySample, *, explicit_run: bool = False,
                             as_of: str | None = None) -> ReadCheck:
        """Check one validity batch using an explicit Run or a fixed default-selection instant.

        Inputs share the single-read scope builder. Missing identity fields raise
        KeyError; request/contract errors propagate; business differences are returned.
        """
        row = sample.validity
        args = validity_arguments(sample, "ts", as_of=as_of, explicit_run=explicit_run)
        args["factor_refs"] = [args.pop("factor_ref")]
        page = read_tool_page(self.api.validity_batch(args)); items = page.items
        issues=[]
        if len(items) != 1: issues.append("validity_batch:cardinality")
        if items:
            item = items[0]
            if item.get("factor_ref") != args["factor_refs"][0] or item.get("success") is not True: issues.append("validity_batch:identity_or_success")
            data = item.get("data")
            if not isinstance(data, dict) or data.get("id") != row.get("id"): issues.append("validity_batch:data")
        return ReadCheck(1, tuple(issues))
