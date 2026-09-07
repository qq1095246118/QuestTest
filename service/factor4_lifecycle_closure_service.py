"""Additional read-only closure checks over persisted Factor 4.0 lifecycle results."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from functools import cmp_to_key
from itertools import combinations
import json
from typing import Any

from db.factor4_lifecycle_repository import LifecycleSnapshot
from service.factor4_read_service import ReadCheck, ReadPrecondition

_SUCCESS = {"success", "completed"}
_METRIC_KEY = ("factor_ref", "factor_type", "factor_id", "factor_version", "market_scope", "label_kind",
               "label_code", "evaluation_type", "interval", "return_bar_interval", "forward_return_bars",
               "window_scope", "sample_start_date", "sample_end_date")
_INPUT_METRIC_KEY = tuple(field for field in _METRIC_KEY if field != "factor_version")
_METRIC_VALUES = ("metric_status", "is_valid", "total_sample_count", "valid_sample_count", "coverage_rate",
                  "mean_ic", "mean_rank_ic", "icir", "rank_icir", "t_stat", "oos_retention", "net_return",
                  "sharpe", "max_drawdown", "turnover_rate", "time_series_score", "cross_sectional_score",
                  "routing_score", "confidence", "scoring_version", "error_code")
_ROUTE_KEY = ("market_scope", "route_profile_key", "environment_date", "label_kind", "label_code",
              "as_of_time", "factor_ref", "factor_type", "factor_id", "factor_version")
_ROUTE_VALUES = ("rank_no", "routing_score", "confidence", "time_series_score", "cross_sectional_score",
                 "is_eligible", "reject_reason_code", "score_rule_version")
_FROZEN_FIELDS = ("market_scope", "route_profile_key", "label_kind", "start_date", "end_date", "as_of_time",
                  "environment_snapshot", "environment_snapshot_hash", "factor_set_snapshot",
                  "factor_set_snapshot_hash", "evaluation_config", "evaluation_config_version",
                  "score_rule_version", "code_version")
_JSON_FIELDS = {"environment_snapshot", "factor_set_snapshot", "evaluation_config"}
_RELATION_FIELDS = ("relation_version", "relation_snapshot_hash", "relationship_version")


class Factor4LifecycleClosureService:
    """Compare producer-owned final evidence without launching calculation or publication."""

    @staticmethod
    def check_independent_recalculations(snapshot: LifecycleSnapshot) -> ReadCheck:
        """Compare distinct successful batches with identical frozen inputs and versions.

        Input is the repository's coherent lifecycle snapshot. Output lists numerical,
        formula-version, membership or ranking differences, excluding only enumerated run
        identities. All metric pairs must first carry identical nonempty producer
        ``artifact_manifest_hash`` values; config declarations alone are insufficient.
        Pairs with different or unknown actual input identities are not compared.
        The frozen environment schema is not the raw-data schema: raw_schema_version
        is checked separately when declared, and this check does not validate raw
        schema content. Missing two distinct batches, input identity or final rows raises
        ``ReadPrecondition``; a known mismatch always takes precedence over that block.
        This observes two persisted calculations, never a repeat read of one batch.
        """
        groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
        for batch in snapshot.batches:
            if batch.get("status") not in _SUCCESS or not batch.get("finished_at") or not batch.get("batch_uid"):
                continue
            if any(batch.get(key) in (None, "") for key in _FROZEN_FIELDS):
                continue
            frozen = {key: _object(batch[key]) if key in _JSON_FIELDS else batch[key] for key in _FROZEN_FIELDS}
            raw_input = _raw_input_identity(frozen["evaluation_config"])
            if not all(frozen[key] for key in _JSON_FIELDS) or not _schema_versions(frozen) or raw_input is None:
                continue
            frozen["raw_input_identity"] = raw_input
            groups[_freeze(frozen)].append(batch)
        issues: list[str] = []
        checked, missing = 0, False
        for batches in groups.values():
            for first, second in combinations(batches, 2):
                if first["id"] == second["id"] or first["batch_uid"] == second["batch_uid"]:
                    continue
                first_metrics = [row for row in snapshot.metrics if row.get("eval_batch_id") == first["id"]]
                second_metrics = [row for row in snapshot.metrics if row.get("eval_batch_id") == second["id"]]
                if not _same_actual_metric_inputs(first_metrics, second_metrics):
                    continue
                prefix = f"recalculation:batches={first['id']},{second['id']}:"
                compared = False
                for kind, keys, values in (("metrics", _METRIC_KEY, _METRIC_VALUES), ("routes", _ROUTE_KEY, _ROUTE_VALUES)):
                    left = [row for row in getattr(snapshot, kind) if row.get("eval_batch_id") == first["id"]]
                    right = [row for row in getattr(snapshot, kind) if row.get("eval_batch_id") == second["id"]]
                    if kind == "routes":
                        left = [_route_projection(row, first) for row in left]
                        right = [_route_projection(row, second) for row in right]
                    if not left or not right:
                        missing = True
                        if bool(left) != bool(right):
                            issues.append(prefix + kind + "_member_set")
                        continue
                    compared = True
                    if any(row.get(field) is None for row in (*left, *right) for field in keys):
                        missing = True
                    before, after = _rows_by_key(left, keys), _rows_by_key(right, keys)
                    if len(before) != len(left) or len(after) != len(right):
                        issues.append(prefix + kind + "_duplicate_business_key")
                    if set(before) != set(after):
                        issues.append(prefix + kind + "_member_set")
                    for key in before.keys() & after.keys():
                        a, b = before[key], after[key]
                        for field in values:
                            if field not in a or field not in b:
                                missing = True
                            elif _freeze(a[field]) != _freeze(b[field]):
                                issues.append(prefix + kind + ":field=" + field)
                        if kind == "metrics":
                            for field in ("metric_identity", "sample_day_count", "oos", "direction"):
                                pa, pb = _object(a.get("metric_payload")), _object(b.get("metric_payload"))
                                if field == "metric_identity":
                                    ia, ib = _object(pa.get(field)), _object(pb.get(field))
                                    names = ("formula_hash", "formula_version", "definition_factor_version", "factor_version")
                                    if not ia or not ib or any(not identity.get(name) for identity in (ia, ib) for name in ("formula_hash", "formula_version")):
                                        missing = True
                                    if not ia.get("eval_batch_uid") or not ib.get("eval_batch_uid"):
                                        missing = True
                                    elif ia["eval_batch_uid"] != first["batch_uid"] or ib["eval_batch_uid"] != second["batch_uid"]:
                                        issues.append(prefix + "metric_batch_execution_identity")
                                    if any(ia.get(name) is not None and ib.get(name) is not None and _freeze(ia[name]) != _freeze(ib[name]) for name in names):
                                        issues.append(prefix + "formula_identity")
                                elif _freeze(pa.get(field)) != _freeze(pb.get(field)):
                                    issues.append(prefix + "metric_payload:" + field)
                        else:
                            ma = next((m for m in snapshot.metrics if m.get("id") == a.get("metric_id") and m.get("eval_batch_id") == first["id"]), None)
                            mb = next((m for m in snapshot.metrics if m.get("id") == b.get("metric_id") and m.get("eval_batch_id") == second["id"]), None)
                            if ma is None or mb is None or _row_key(ma, _METRIC_KEY) != _row_key(mb, _METRIC_KEY):
                                issues.append(prefix + "route_metric_reference")
                checked += int(compared)
        return _finish(checked, issues, missing, "no complete distinct successful calculations with identical actual metric artifact_manifest_hash and raw input identity/frozen inputs/config/code/declared schema/as_of")

    @staticmethod
    def check_parent_relation_evidence(snapshot: LifecycleSnapshot) -> ReadCheck:
        """Reconcile frozen parent child identities and relation versions with metric evidence.

        Only explicit ``metric_identity.children`` and matching relation identity fields
        are compared with frozen members. No child values are synthesized and no parent
        IC mean is treated as an aggregation oracle. Missing evidence raises
        ``ReadPrecondition`` unless another parent has a confirmed contradiction.
        """
        issues: list[str] = []
        checked, missing = 0, False
        for batch in snapshot.batches:
            if batch.get("status") not in _SUCCESS:
                continue
            members = _object(batch.get("factor_set_snapshot")).get("members", [])
            if not isinstance(members, list):
                continue
            for parent in (m for m in members if isinstance(m, Mapping) and m.get("factor_type") == "factor"):
                children = parent.get("children")
                rows = [m for m in snapshot.metrics if m.get("eval_batch_id") == batch.get("id") and m.get("factor_ref") == parent.get("factor_ref")]
                prefix = f"parent:batch={batch.get('id')}:factor={parent.get('factor_ref')}:"
                if not isinstance(children, list) or not children or not rows:
                    missing = True
                    continue
                if any(not isinstance(c, Mapping) or not c.get("factor_ref") or not c.get("factor_version") for c in children):
                    missing = True
                    continue
                expected = {c["factor_ref"]: c for c in children}
                if len(expected) != len(children):
                    issues.append(prefix + "duplicate_frozen_child")
                version_fields = [name for name in _RELATION_FIELDS if parent.get(name) is not None]
                if not version_fields:
                    missing = True
                for row in rows:
                    identity = _object(_object(row.get("metric_payload")).get("metric_identity"))
                    actual = identity.get("children")
                    if not isinstance(actual, list) or any(not isinstance(c, Mapping) for c in actual):
                        missing = True
                        continue
                    checked += 1
                    by_ref = {c.get("factor_ref"): c for c in actual}
                    if len(by_ref) != len(actual) or set(by_ref) != set(expected):
                        issues.append(prefix + "child_membership")
                    for ref in by_ref.keys() & expected.keys():
                        for field in ("factor_version", "weight", "direction"):
                            if field in expected[ref] and field not in by_ref[ref]:
                                missing = True
                            elif field in expected[ref] and _freeze(expected[ref][field]) != _freeze(by_ref[ref].get(field)):
                                issues.append(prefix + "child_" + field)
                    for field in version_fields:
                        if field not in identity:
                            missing = True
                        elif identity[field] != parent[field]:
                            issues.append(prefix + field)
                    if not identity.get("eval_batch_uid") or not batch.get("batch_uid"):
                        missing = True
                    elif identity["eval_batch_uid"] != batch["batch_uid"]:
                        issues.append(prefix + "evidence_batch_uid")
                    if not identity.get("definition_factor_version") or not parent.get("factor_version"):
                        missing = True
                    elif identity["definition_factor_version"] != parent["factor_version"]:
                        issues.append(prefix + "definition_factor_version")
        return _finish(checked, issues, missing, "parent metric identity lacks frozen child/version/weight/relation evidence")

    @staticmethod
    def check_unsuccessful_publication_final_state(snapshot: LifecycleSnapshot, outcome: str) -> ReadCheck:
        """Check failed, cancelled or rolled-back terminal records and all active pointers.

        ``outcome`` must be failed/cancelled/rolled_back, otherwise ``ValueError``.
        Returns persisted final-state inconsistencies; absence of that real outcome
        raises ``ReadPrecondition`` only after active-pointer failures are retained.
        This does not observe transaction boundaries, events or rollback atomicity.
        """
        if outcome not in {"failed", "cancelled", "rolled_back"}:
            raise ValueError("outcome must be failed, cancelled or rolled_back")
        aliases = {"failed": {"failed"}, "cancelled": {"cancelled", "canceled"}, "rolled_back": {"rolled_back", "rollback"}}[outcome]
        selected = [b for b in snapshot.batches if b.get("status") in aliases or b.get("publish_status") in aliases]
        issues: list[str] = []
        batches = {b.get("id"): b for b in snapshot.batches}
        pointers: dict[tuple[Any, ...], int] = defaultdict(int)
        for batch in snapshot.batches:
            prefix = f"batch:id={batch.get('id')}:"
            if batch.get("is_active"):
                pointers[(batch.get("market_scope"), batch.get("route_profile_key"))] += 1
                if batch.get("status") not in _SUCCESS or batch.get("publish_status") != "published":
                    issues.append(prefix + "active_nonpublished_terminal")
                if not batch.get("active_scope_key"):
                    issues.append(prefix + "active_scope_pointer_missing")
            elif batch.get("active_scope_key") is not None:
                issues.append(prefix + "inactive_scope_pointer_retained")
        if any(count > 1 for count in pointers.values()):
            issues.append("publication:multiple_active_batches_in_partition")
        for batch in selected:
            if batch.get("is_active") or batch.get("active_scope_key") is not None:
                issues.append(f"batch:id={batch.get('id')}:unsuccessful_terminal_active")
        for route in snapshot.routes:
            if not route.get("is_active"):
                continue
            batch = batches.get(route.get("eval_batch_id"))
            prefix = f"route:id={route.get('id')}:"
            if batch is None or not batch.get("is_active"):
                issues.append(prefix + "active_route_without_active_batch")
                continue
            projected = _route_projection(route, batch)
            if any(projected.get(name) != batch.get(name) for name in ("publication_uid", "publish_version", "market_scope", "route_profile_key")):
                issues.append(prefix + "active_pointer_identity")
        return _finish(len(selected), issues, False, f"no persisted {outcome} publication terminal state; atomicity is not observed")

    @staticmethod
    def check_declared_tie_breaker(snapshot: LifecycleSnapshot) -> ReadCheck:
        """Apply only a producer's frozen ``ranking_contract.tie_breaker`` declaration.

        The declaration is an ordered list of field/direction objects. Supported
        scalar fields are restricted to persisted route columns; unknown syntax or
        no declaration raises BLOCKED_DOC, never an invented factor-ref ordering.
        Missing tied rows or tie-field values raises a data precondition. Returns
        contradictions first if another partition cannot be checked.
        """
        numeric = {"confidence", "factor_id", "time_series_score", "cross_sectional_score"}
        allowed = numeric | {"factor_ref", "factor_version"}
        issues: list[str] = []
        checked, missing, undocumented = 0, False, False
        for batch in snapshot.batches:
            groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
            for route in snapshot.routes:
                if route.get("eval_batch_id") == batch.get("id") and route.get("is_eligible"):
                    projected = _route_projection(route, batch)
                    groups[_row_key(projected, ("market_scope", "route_profile_key", "label_kind", "label_code", "environment_date", "as_of_time", "routing_score"))].append(projected)
            tied_groups = [rows for rows in groups.values() if len(rows) > 1]
            if not tied_groups:
                continue
            contract = _object(_object(batch.get("evaluation_config")).get("ranking_contract"))
            rules = contract.get("tie_breaker")
            if not isinstance(rules, list) or not rules or any(not isinstance(r, Mapping) or r.get("field") not in allowed or r.get("direction") not in {"asc", "desc"} for r in rules):
                undocumented = True
                continue
            for tied in tied_groups:
                parsed: dict[int, tuple[Any, ...]] = {}
                for route in tied:
                    try:
                        vals = tuple(Decimal(str(route[r["field"]])) if r["field"] in numeric else route[r["field"]] for r in rules)
                        if any(not isinstance(v, (str, Decimal)) or isinstance(v, Decimal) and not v.is_finite() for v in vals):
                            raise ValueError("missing/nonfinite tie value")
                        parsed[route["id"]] = vals
                    except (KeyError, TypeError, ValueError, ArithmeticError):
                        missing = True
                if len(parsed) != len(tied):
                    continue
                if len(set(parsed.values())) != len(tied):
                    undocumented = True
                    continue
                def compare(left: Mapping[str, Any], right: Mapping[str, Any]) -> int:
                    for a, b, rule in zip(parsed[left["id"]], parsed[right["id"]], rules, strict=True):
                        if a != b:
                            return (1 if a > b else -1) * (1 if rule["direction"] == "asc" else -1)
                    return 0
                if any(type(r.get("rank_no")) is not int for r in tied):
                    missing = True
                    continue
                if len({r["rank_no"] for r in tied}) != len(tied) or min(r["rank_no"] for r in tied) < 1:
                    issues.append(f"batch:id={batch.get('id')}:tied_rank_invalid")
                checked += 1
                expected = [r["id"] for r in sorted(tied, key=cmp_to_key(compare))]
                actual = [r["id"] for r in sorted(tied, key=lambda r: r["rank_no"])]
                if expected != actual:
                    issues.append(f"batch:id={batch.get('id')}:declared_tie_breaker_order")
        if issues:
            return ReadCheck(max(checked, 1), tuple(issues))
        if undocumented:
            raise ReadPrecondition("BLOCKED_DOC: no complete producer-declared tie-breaker for all tied partitions")
        return _finish(checked, issues, missing, "no complete tied route group for declared tie-breaker")


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple(sorted((str(k), _freeze(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    return value


def _schema_versions(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "schema_version" and isinstance(item, str) and item:
                result.add(item)
            result.update(_schema_versions(item))
    elif isinstance(value, list):
        for item in value:
            result.update(_schema_versions(item))
    return result


def _raw_input_identity(config: Mapping[str, Any]) -> Any | None:
    """Require a producer-owned market-data input identity, not an environment-label hash."""
    declared = {key: config[key] for key in ("input_snapshot_hash", "data_snapshot_hash") if isinstance(config.get(key), str) and config[key].strip()}
    for key in ("input_snapshot", "data_snapshot"):
        value = config.get(key)
        if isinstance(value, Mapping) and (value.get("hash") or value.get("snapshot_hash")):
            declared[key] = value
    return _freeze(declared) if declared else None


def _same_actual_metric_inputs(left: Sequence[Mapping[str, Any]], right: Sequence[Mapping[str, Any]]) -> bool:
    """Establish the complete actual-input predicate before judging any result difference.

    Executable factor_version is deliberately compared as an output identity later;
    the input scope uses the remaining formal metric dimensions. Hashes from config
    or the daily-label schema are never substituted for per-metric manifest evidence.
    """
    if not left or not right:
        return False
    before, after = _rows_by_key(left, _INPUT_METRIC_KEY), _rows_by_key(right, _INPUT_METRIC_KEY)
    if len(before) != len(left) or len(after) != len(right) or before.keys() != after.keys():
        return False
    for key in before:
        if any(value is None for value in key):
            return False
        identities = tuple(_object(_object(row.get("metric_payload")).get("metric_identity")) for row in (before[key], after[key]))
        hashes = tuple(identity.get("artifact_manifest_hash") for identity in identities)
        if any(not isinstance(value, str) or not value.strip() for value in hashes) or hashes[0] != hashes[1]:
            return False
        if any("raw_schema_version" in identity for identity in identities):
            schemas = tuple(identity.get("raw_schema_version") for identity in identities)
            if any(not isinstance(value, str) or not value.strip() for value in schemas) or schemas[0] != schemas[1]:
                return False
    return True


def _row_key(row: Mapping[str, Any], names: Sequence[str]) -> tuple[Any, ...]:
    return tuple(_freeze(row.get(name)) for name in names)


def _route_projection(row: Mapping[str, Any], batch: Mapping[str, Any]) -> dict[str, Any]:
    """Derive profile from its owner when the physical route table omits that column.

    The calculation repository projects this field through its batch join; lifecycle
    uses SELECT * on the route table. An explicitly present route value, including
    null, is retained for comparison rather than replaced by the batch value.
    """
    result = dict(row)
    if "route_profile_key" not in result:
        result["route_profile_key"] = batch.get("route_profile_key")
    return result


def _rows_by_key(rows: Sequence[Mapping[str, Any]], names: Sequence[str]) -> dict[tuple[Any, ...], Mapping[str, Any]]:
    return {_row_key(row, names): row for row in rows}


def _finish(checked: int, issues: Sequence[str], missing: bool, reason: str) -> ReadCheck:
    if issues:
        return ReadCheck(max(checked, 1), tuple(dict.fromkeys(issues)))
    if not checked or missing:
        raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: " + reason)
    return ReadCheck(checked, ())
