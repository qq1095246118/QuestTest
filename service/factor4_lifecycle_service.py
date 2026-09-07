"""Business assertions over frozen membership and publication history, without writes."""

import json
from collections import Counter, defaultdict
from collections.abc import Mapping
from typing import Any

from api.factor_data_mcp_api import FactorDataMCPAPI
from db.factor4_lifecycle_repository import LifecycleSnapshot
from service.factor4_read_service import ReadCheck, ReadContractError, ReadPrecondition, read_tool_page


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, (str, bytes)):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            raise ReadContractError("lifecycle JSON is not parseable") from None
    if not isinstance(value, dict):
        raise ReadContractError("lifecycle payload must be an object")
    return value


class Factor4LifecycleService:
    """Pure reconciliation of final lifecycle data; no reference to tmp or reports."""

    def check_entity_schema(self, inventory: tuple[dict[str, Any], ...]) -> ReadCheck:
        """Check persisted entity identity columns and revision uniqueness, returning only schema issues."""
        required = {
            "market_environment_daily": {"id", "environment_date", "label_kind", "revision", "is_current", "available_at"},
            "market_environment_eval_batch": {"id", "batch_uid", "status", "publish_status", "publication_uid"},
            "market_environment_factor_metric": {"id", "eval_batch_id", "factor_ref", "factor_version", "evaluation_type"},
            "market_environment_factor_route": {"id", "eval_batch_id", "metric_id", "publication_uid", "is_active"},
            "market_environment_strategy_feedback_submissions": {"id", "submission_id", "status"},
        }
        columns: dict[str, set[str]] = defaultdict(set)
        indexes: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in inventory:
            columns[row["table_name"]].add(row["column_name"])
            if row.get("index_name") and row.get("non_unique") == 0:
                indexes[(row["table_name"], row["index_name"])].append(row)
        issues = [f"schema:{table}:missing={column}" for table, names in required.items()
                  for column in sorted(names - columns[table])]
        revision_key = {"environment_date", "label_kind", "revision"}
        if not any(table == "market_environment_daily" and {r["column_name"] for r in rows} == revision_key
                   and len(rows) == len(revision_key) for (table, _), rows in indexes.items()):
            issues.append("schema:daily:revision_unique_key_missing")
        return ReadCheck(len(required), tuple(issues))

    def check_credential_exposure(self, counts: tuple[dict[str, Any], ...]) -> ReadCheck:
        """Reject token-shaped payload values; incomplete scans block rather than certify no exposure."""
        issues = tuple(f"credential_shape:{row['table']}:{row['column']}:count={row['hits']}"
                       for row in counts if row.get("hits"))
        if not issues and (not counts or any(row.get("omitted") for row in counts)):
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: payload scan incomplete at bounded table limit")
        return ReadCheck(len(counts), issues)

    def check_metric_units(self, snapshot: LifecycleSnapshot) -> ReadCheck:
        """Require each persisted final metric unit to be unique and point to an existing batch."""
        if not snapshot.metrics:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no final metric units")
        names = ("eval_batch_id", "factor_ref", "factor_version", "label_code", "evaluation_type",
                 "interval", "return_bar_interval", "forward_return_bars", "window_scope")
        counts = Counter(tuple(row.get(key) for key in names) for row in snapshot.metrics)
        batches = {row["id"] for row in snapshot.batches}
        issues = []
        for row in snapshot.metrics:
            prefix = f"metric:id={row['id']}:"
            if counts[tuple(row.get(key) for key in names)] > 1:
                issues.append(prefix + "duplicate_formal_unit")
            if row.get("eval_batch_id") not in batches:
                issues.append(prefix + "missing_batch")
        return ReadCheck(len(snapshot.metrics), tuple(issues))

    def check_batch_terminal_counts(self, snapshot: LifecycleSnapshot) -> ReadCheck:
        """Reconcile terminal counters with all same-snapshot metric statuses.

        Input is a consistent read of batches and metrics; return counter/time
        contradictions. Missing batches raise ReadPrecondition. Running batches
        keep range checks only; no computation or publication atomicity is assumed.
        """
        if not snapshot.batches:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no final batches")
        issues = []
        fields = ("completed_metric_count", "insufficient_metric_count", "failed_metric_count")
        terminal_states = {"success", "completed", "partial_fail", "failed", "cancelled", "canceled"}
        metric_counts: dict[int, Counter[str | None]] = defaultdict(Counter)
        for metric in snapshot.metrics:
            metric_counts[metric["eval_batch_id"]][metric.get("metric_status")] += 1
        for row in snapshot.batches:
            prefix = f"batch:id={row['id']}:"
            if row.get("publish_status") in {"published", "active"} and row.get("published_at") is None:
                issues.append(prefix + "published_without_time")
            if row.get("status") in terminal_states and row.get("finished_at") is None:
                issues.append(prefix + "terminal_without_time")
            counters = [row.get(key) for key in (*fields, "expected_metric_count")]
            if any(type(value) is not int or value < 0 for value in counters):
                issues.append(prefix + "invalid_metric_counter")
            elif sum(counters[:3]) > counters[3]:
                issues.append(prefix + "metric_counts_exceed_expected")
            if row.get("status") in terminal_states:
                actual = metric_counts[row["id"]]
                for field, status in zip(fields, ("success", "insufficient_sample", "failed"), strict=True):
                    if type(row.get(field)) is int and row[field] != actual[status]:
                        issues.append(prefix + f"{field}:declared={row[field]}:actual={actual[status]}")
        return ReadCheck(len(snapshot.batches), tuple(issues))

    def check_ineligible_factors_remain_queryable(self, snapshot: LifecycleSnapshot, mcp: FactorDataMCPAPI) -> ReadCheck:
        """Read real double-invalid factor definitions; missing samples block, endpoint failures propagate."""
        active = {row["id"] for row in snapshot.batches if row.get("is_active") and row.get("publish_status") == "published"}
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for row in snapshot.metrics:
            if row["eval_batch_id"] in active and row.get("evaluation_type") in {"time_series", "cross_sectional"}:
                groups[(row["eval_batch_id"], row["factor_ref"], row["factor_version"], row["label_code"])].append(row)
        invalid = [rows for rows in groups.values()
                   if {row["evaluation_type"] for row in rows} == {"time_series", "cross_sectional"}
                   and not any(row.get("metric_status") == "success" and row.get("is_valid") == 1 for row in rows)]
        refs = sorted({rows[0]["factor_ref"] for rows in invalid})[:5]
        if not refs:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no published double-invalid factor pair")
        page = read_tool_page(mcp.get_factor_details_batch(refs, detail_level="summary"))
        items = {item.get("factor_ref"): item for item in page.items}
        issues = []
        if set(items) != set(refs) or len(page.items) != len(refs):
            issues.append("invalid_detail:batch_membership")
        for ref in refs:
            item = items.get(ref, {})
            detail = item.get("data")
            if item.get("error") is not None or not isinstance(detail, Mapping) or detail.get("factor_ref") != ref:
                issues.append("invalid_detail:factor_unavailable")
        return ReadCheck(len(refs), tuple(issues))

    def check_publication_mode_contract(self, snapshot: LifecycleSnapshot) -> ReadCheck:
        """Read frozen publication semantics before deciding atomic/incremental expectations.

        Missing producer-owned semantics raise BLOCKED_DOC instead of inferring atomic
        publication from schema comments. Explicit contradictory declarations fail.
        """
        if not snapshot.batches:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no batch publication contract")
        required = {"partial_environment_visibility", "partial_factor_visibility", "route_admission_condition",
                    "publication_identity_stability", "history_retention", "repeat_publish_semantics", "rollback_semantics"}
        issues: list[str] = []
        missing = False
        for batch in snapshot.batches:
            config = _object(batch.get("evaluation_config"))
            mode = config.get("publication_mode")
            contract = config.get("publication_contract")
            if not isinstance(contract, dict):
                contract = config
            if mode is None or not required <= set(contract) or not contract.get("allowed_publish_states"):
                missing = True
                continue
            allowed = contract["allowed_publish_states"]
            if not isinstance(allowed, list) or batch["publish_status"] not in allowed:
                issues.append(f"batch:id={batch['id']}:publish_state_not_declared")
            if mode == "atomic" and any(contract[key] is True for key in ("partial_environment_visibility", "partial_factor_visibility")):
                issues.append(f"batch:id={batch['id']}:contradictory_atomic_visibility")
        if not issues and missing:
            raise ReadPrecondition("BLOCKED_DOC: frozen publication config lacks complete mode/history/rollback semantics")
        return ReadCheck(len(snapshot.batches), tuple(issues))

    def check_route_history(self, snapshot: LifecycleSnapshot, *, require_superseded: bool = False) -> ReadCheck:
        """Check all route FK/version identities and active uniqueness; missing history is a precondition.

        A real identity failure is returned before checking whether historical samples
        exist, so absence of a superseded batch cannot hide a current defect.
        """
        if not snapshot.routes:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no route history")
        batches = {r["id"]: r for r in snapshot.batches}
        metrics = {r["id"]: r for r in snapshot.metrics}
        active_keys: set[tuple[Any, ...]] = set()
        versions: dict[str, set[tuple[Any, ...]]] = defaultdict(set)
        issues: list[str] = []
        for row in snapshot.routes:
            batch, metric = batches.get(row["eval_batch_id"]), metrics.get(row["metric_id"])
            prefix = f"route:id={row['id']}:"
            if batch is None or metric is None:
                issues.append(prefix + "missing_reference")
                continue
            for name in ("eval_batch_id", "factor_ref", "factor_version", "market_scope", "label_kind", "label_code"):
                if row.get(name) != metric.get(name):
                    issues.append(prefix + name)
            for name in ("publication_uid", "publish_version"):
                if row.get(name) != batch.get(name):
                    issues.append(prefix + name)
            if row.get("is_active"):
                key = (batch["market_scope"], batch["route_profile_key"], row["label_kind"], row["label_code"], row["factor_ref"], row["factor_version"])
                if key in active_keys:
                    issues.append(prefix + "duplicate_active_identity")
                active_keys.add(key)
                versions[row["publication_uid"]].add((row["publish_version"], row["eval_batch_id"]))
                if not batch.get("is_active"):
                    issues.append(prefix + "active_route_in_inactive_batch")
        if any(len(values) != 1 for values in versions.values()):
            issues.append("route:multiple_active_publication_versions")
        if require_superseded and not issues:
            retained = [b for b in snapshot.batches if not b.get("is_active") and b.get("published_at")
                        and any(r["eval_batch_id"] == b["id"] for r in snapshot.routes)]
            if not retained:
                raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no superseded publication with retained routes")
            for batch in retained:
                if any(r.get("is_active") for r in snapshot.routes if r["eval_batch_id"] == batch["id"]):
                    issues.append(f"batch:id={batch['id']}:superseded_routes_active")
        return ReadCheck(len(snapshot.routes), tuple(issues))

    def check_frozen_membership(self, snapshot: LifecycleSnapshot, *, parents: bool = False) -> ReadCheck:
        """Compare terminal metrics with frozen members, not today's mutable relationships.

        Definition version and executable version are different identities. Parent
        results require a terminal parent batch; cancelled snapshots cannot pass it.
        """
        issues: list[str] = []
        checked = 0
        for batch in snapshot.batches:
            if batch["status"] not in {"success", "completed"}:
                continue
            frozen = _object(batch["factor_set_snapshot"])
            members = frozen.get("members")
            if not isinstance(members, list) or not members or any(not isinstance(m, dict) for m in members):
                issues.append(f"batch:id={batch['id']}:missing_members")
                continue
            has_parents = any(m.get("factor_type") == "factor" for m in members)
            if has_parents != parents:
                continue
            checked += 1
            by_ref = {m.get("factor_ref"): m for m in members}
            prefix = f"batch:id={batch['id']}:"
            if len(by_ref) != len(members) or None in by_ref:
                issues.append(prefix + "duplicate_or_missing_factor_ref")
            if frozen.get("factor_count") is not None and frozen["factor_count"] != len(members):
                issues.append(prefix + "factor_count")
            if not batch.get("factor_set_snapshot_hash"):
                issues.append(prefix + "snapshot_hash_missing")
            for member in members:
                if not member.get("factor_version"):
                    issues.append(prefix + "member_version_missing")
                if member.get("factor_type") == "factor":
                    children = member.get("children")
                    if not isinstance(children, list) or not children or any(not isinstance(c, dict) for c in children):
                        issues.append(prefix + "parent_children_missing")
                        continue
                    if len({c.get("factor_ref") for c in children}) != len(children) or any(not c.get("factor_version") for c in children):
                        issues.append(prefix + "child_identity_or_version")
                    if member.get("child_count") is not None and member["child_count"] != len(children):
                        issues.append(prefix + "child_count")
            rows = [m for m in snapshot.metrics if m["eval_batch_id"] == batch["id"]]
            if {m["factor_ref"] for m in rows} != set(by_ref):
                issues.append(prefix + "metric_member_set")
            versions: dict[str, set[str]] = defaultdict(set)
            for row in rows:
                member = by_ref.get(row["factor_ref"])
                if member is None:
                    continue
                identity = _object(row["metric_payload"]).get("metric_identity")
                if not isinstance(identity, Mapping):
                    issues.append(f"metric:id={row['id']}:identity_missing")
                    continue
                expected = {"definition_factor_version": member.get("factor_version"),
                            "factor_version": row.get("factor_version"), "eval_batch_uid": batch["batch_uid"],
                            "evaluation_config_version": batch["evaluation_config_version"]}
                for name, value in expected.items():
                    if value is None or identity.get(name) != value:
                        issues.append(f"metric:id={row['id']}:identity={name}")
                for name in ("factor_type", "factor_id"):
                    if str(row.get(name)) != str(member.get(name)):
                        issues.append(f"metric:id={row['id']}:member={name}")
                versions[row["factor_ref"]].add(str(row.get("factor_version")))
            if any(len(v) > 1 for v in versions.values()):
                issues.append(prefix + "executable_version_conflict")
        if not checked and not issues:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no terminal parent batch" if parents else "BLOCKED_DATA_PRECONDITION: no terminal direct batch")
        return ReadCheck(max(checked, len(issues)), tuple(issues))

    def check_audit_fields(self, snapshot: LifecycleSnapshot, stage: str) -> ReadCheck:
        """Require nonempty correlation/actor/time/version in one populated stage; no raw values returned."""
        summary = snapshot.audits.get(stage)
        if summary is None:
            raise ValueError("unknown audit stage")
        if not summary.get("total"):
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: audit stage has no records")
        issues = tuple(f"audit:{stage}:{name}:count={int(count)}" for name, count in summary.items()
                       if name.startswith("missing_") and count)
        return ReadCheck(int(summary["total"]), issues)

    def check_terminal_replay(self, first: LifecycleSnapshot, second: LifecycleSnapshot) -> ReadCheck:
        """Compare only same-ID terminal results across reads; new batches are not a mutation defect."""
        terminal = {b["id"]: b for b in first.batches if b["status"] in {"success", "completed"}}
        repeated = {b["id"]: b for b in second.batches}
        if not terminal:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no terminal batch for replay")
        issues: list[str] = []
        drifted = 0
        frozen_keys = ("batch_uid", "factor_set_snapshot_hash", "environment_snapshot_hash", "factor_set_snapshot",
                       "environment_snapshot", "evaluation_config", "evaluation_config_version", "score_rule_version", "code_version")
        for identifier, batch in terminal.items():
            other = repeated.get(identifier)
            if other is None:
                issues.append(f"batch:id={identifier}:terminal_record_removed")
                continue
            if any(other.get(key) != batch.get(key) for key in ("publish_version", "publish_status", "is_active")):
                drifted += 1
                continue
            for key in frozen_keys:
                if other.get(key) != batch.get(key):
                    issues.append(f"batch:id={identifier}:frozen_field={key}")
            for label in ("metrics", "routes"):
                before = tuple(r for r in getattr(first, label) if r["eval_batch_id"] == identifier)
                after = tuple(r for r in getattr(second, label) if r["eval_batch_id"] == identifier)
                if before != after:
                    issues.append(f"batch:id={identifier}:terminal_{label}_changed")
        if drifted and not issues:
            raise ReadPrecondition("SNAPSHOT_DRIFT: publication changed during final-result replay")
        return ReadCheck(len(terminal), tuple(issues))
