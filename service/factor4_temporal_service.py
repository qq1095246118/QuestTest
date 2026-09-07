"""Final environment membership and temporal result evidence; no raw series replay."""

import json
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from db.factor4_lifecycle_repository import LifecycleSnapshot
from db.factor4_read_repository import DailyReadSnapshot
from service.factor4_read_service import LABELS, ReadCheck, ReadContractError, ReadPrecondition
from service.factor4_recommendation_service import lifecycle_time


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, (str, bytes)):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            raise ReadContractError("temporal payload is not JSON") from None
    if not isinstance(value, dict):
        raise ReadContractError("temporal payload is not an object")
    return value


def calendar_segments(values: list[str]) -> list[dict[str, Any]]:
    """Return maximal contiguous day segments; reject duplicate dates and invalid ISO dates."""
    dates = sorted(date.fromisoformat(v) for v in values)
    if len(set(dates)) != len(dates):
        raise ValueError("duplicate calendar day")
    segments: list[list[date]] = []
    for day in dates:
        if not segments or day != segments[-1][1] + timedelta(days=1):
            segments.append([day, day])
        else:
            segments[-1][1] = day
    return [{"start_date": start.isoformat(), "end_date": end.isoformat(), "day_count": (end - start).days + 1}
            for start, end in segments]


class Factor4TemporalService:
    """Compare frozen final metadata with DB revisions and documented chronological constraints."""

    def check_environment_partitions(
        self, snapshot: LifecycleSnapshot, daily: DailyReadSnapshot, *, label: str,
    ) -> ReadCheck:
        """Rebuild each label's date segments and compare persisted metric sample membership.

        Frozen revision IDs are checked against all DB revisions, not the current flag.
        Missing records or malformed required fields fail; absence of any terminal
        publication is a data precondition. This does not verify per-bar gap economics.
        """
        if label not in LABELS:
            raise ValueError("unknown label")
        batches = [b for b in snapshot.batches if b["status"] in {"success", "completed"}]
        if not batches:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no terminal environment snapshot")
        by_id = {r["id"]: r for r in daily.rows}
        checked = 0
        issues: list[str] = []
        for batch in batches:
            frozen = _object(batch["environment_snapshot"])
            members, partitions = frozen.get("members"), frozen.get("partitions")
            if not isinstance(members, list) or any(not isinstance(m, dict) for m in members) or not isinstance(partitions, dict):
                issues.append(f"batch:id={batch['id']}:environment_snapshot_shape")
                continue
            checked += 1
            prefix = f"batch:id={batch['id']}:label={label}:"
            dates = [str(m.get("environment_date")) for m in members]
            ids = [m.get("daily_id") for m in members]
            if len(set(ids)) != len(ids) or len(set(dates)) != len(dates) or dates != sorted(dates):
                issues.append(prefix + "member_identity_order")
            if set(frozen.get("missing_dates") or []) & set(dates):
                issues.append(prefix + "missing_member_overlap")
            selected = [m for m in members if m.get("label_code") == label]
            expected_dates = [str(m["environment_date"]) for m in selected]
            partition = partitions.get(label)
            if not isinstance(partition, dict):
                issues.append(prefix + "partition_missing")
                continue
            actual_dates = partition.get("dates")
            if actual_dates != expected_dates or partition.get("day_count") != len(expected_dates):
                issues.append(prefix + "partition_dates_or_day_count")
            try:
                if partition.get("segments") != calendar_segments(expected_dates):
                    issues.append(prefix + "calendar_segments")
            except (TypeError, ValueError):
                issues.append(prefix + "invalid_calendar_dates")
            try:
                cutoff = lifecycle_time(frozen.get("as_of_time"))
                if cutoff != lifecycle_time(batch["as_of_time"], database=True):
                    issues.append(prefix + "snapshot_asof")
            except (TypeError, ValueError):
                issues.append(prefix + "snapshot_asof_invalid")
                continue
            for member in selected:
                row = by_id.get(member.get("daily_id"))
                if row is None:
                    issues.append(prefix + "frozen_revision_missing")
                    continue
                for key in ("environment_date", "label_code", "revision", "schema_version"):
                    if str(row.get(key)) != str(member.get(key)):
                        issues.append(prefix + "frozen_revision_field=" + key)
                if row.get("label_kind") != batch["label_kind"]:
                    issues.append(prefix + "frozen_revision_kind")
                try:
                    available = lifecycle_time(member.get("available_at"))
                    if available != lifecycle_time(row["available_at"], database=True) or available > cutoff:
                        issues.append(prefix + "frozen_revision_visibility")
                except (TypeError, ValueError):
                    issues.append(prefix + "frozen_revision_time_invalid")
            for metric in snapshot.metrics:
                if metric["eval_batch_id"] != batch["id"] or metric["label_code"] != label:
                    continue
                payload = _object(metric["metric_payload"])
                count = payload.get("sample_day_count")
                if count is None or int(count) != len(expected_dates):
                    issues.append(f"metric:id={metric['id']}:sample_day_count")
        return ReadCheck(max(checked, len(issues)), tuple(issues))

    def check_result_time_boundaries(self, snapshot: LifecycleSnapshot) -> ReadCheck:
        """Validate final result periods, OOS sequence and direction freeze against frozen as-of.

        Only persisted final evidence is inspected. It cannot establish absence of
        leakage inside unavailable training rows or fitted normalization parameters.
        """
        batches = {b["id"]: b for b in snapshot.batches if b["status"] in {"success", "completed"}}
        issues: list[str] = []
        checked = 0
        successful = 0
        for batch in batches.values():
            cutoff = lifecycle_time(batch["as_of_time"], database=True)
            for member in _object(batch["factor_set_snapshot"]).get("members", []):
                if member.get("updated_at") and lifecycle_time(member["updated_at"]) > cutoff:
                    issues.append(f"batch:id={batch['id']}:future_factor_definition")
        for metric in snapshot.metrics:
            batch = batches.get(metric["eval_batch_id"])
            if batch is None:
                continue
            checked += 1
            prefix = f"metric:id={metric['id']}:"
            if metric["label_kind"] != batch["label_kind"]:
                issues.append(prefix + "label_kind")
            if metric.get("sample_start_date") is not None and str(metric["sample_start_date"]) < str(batch["start_date"]):
                issues.append(prefix + "sample_start")
            if metric.get("sample_end_date") is not None and str(metric["sample_end_date"]) > str(batch["end_date"]):
                issues.append(prefix + "sample_end")
            if metric["metric_status"] != "success":
                continue
            successful += 1
            payload = _object(metric["metric_payload"])
            oos, direction = payload.get("oos"), payload.get("direction")
            folds = oos.get("folds") if isinstance(oos, Mapping) else None
            if not isinstance(folds, list) or not folds or any(not isinstance(fold, dict) for fold in folds):
                issues.append(prefix + "oos_folds_missing")
                continue
            cutoff = lifecycle_time(batch["as_of_time"], database=True)
            previous_end = None
            for fold in folds:
                try:
                    start, end = lifecycle_time(fold.get("start")), lifecycle_time(fold.get("end"))
                    if start >= end or end > cutoff or (previous_end is not None and start != previous_end):
                        issues.append(prefix + "oos_fold_boundary")
                    previous_end = end
                except (TypeError, ValueError):
                    issues.append(prefix + "oos_time_invalid")
            try:
                if not isinstance(direction, Mapping) or lifecycle_time(direction.get("direction_frozen_at")) != lifecycle_time(folds[0].get("start")):
                    issues.append(prefix + "direction_frozen_at")
            except (TypeError, ValueError):
                issues.append(prefix + "direction_frozen_at_invalid")
        if not issues and not successful:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no successful terminal metrics with OOS evidence")
        return ReadCheck(max(checked, len(issues)), tuple(issues))
