"""Offline counterexamples for representative multi-publication MCP output coverage."""

from dataclasses import replace
from datetime import datetime, timezone
import sqlite3
from typing import Any

import pytest

from db.factor4_read_repository import EnvironmentMetricSample, EnvironmentReadMatrixSnapshot, Factor4ReadRepository
from service.factor4_read_service import Factor4ReadService, LABELS, ReadPrecondition, _METRIC_FIELDS, _ROUTE_FIELDS
from tests.unit.test_factor4_read_service import _response
from tests.unit.test_factor4_summary_repository import _DB

pytestmark = pytest.mark.unit
NOW = datetime(2026, 9, 7, tzinfo=timezone.utc)


def _matrix() -> EnvironmentReadMatrixSnapshot:
    batches, samples = [], []
    for identifier, market, profile in ((1, "all", "default"), (2, "large_caps", "other")):
        batch = dict(id=identifier, batch_uid=f"batch-{identifier}", market_scope=market, route_profile_key=profile,
                     publication_uid=f"pub-{identifier}", publish_version=f"version-{identifier}", is_active=1,
                     status="success", publish_status="published", score_rule_version="v1", label_kind="fact",
                     as_of_time=NOW, published_at=NOW)
        batches.append(batch)
        for kind in ("factor", "sub_factor"):
            metrics, routes = [], []
            ref = f"{kind}:{identifier}"
            for index, label in enumerate(LABELS):
                for scope in ("time_series", "cross_sectional"):
                    metric = {key: None for key in _METRIC_FIELDS}
                    metric.update(id=len(metrics) + 1, eval_batch_id=identifier, factor_ref=ref, factor_type=kind,
                                  factor_id=identifier, factor_version="factor-v1", market_scope=market,
                                  label_kind="fact", label_code=label, evaluation_type=scope, mean_ic=0.1)
                    metrics.append(metric)
                route = {key: None for key in _ROUTE_FIELDS}
                route.update(id=index + 1, eval_batch_id=identifier, factor_ref=ref, factor_type=kind,
                             factor_id=identifier, factor_version="factor-v1", label_code=label, label_kind="fact",
                             publication_uid=batch["publication_uid"], publish_version=batch["publish_version"],
                             market_scope=market, is_active=1, is_eligible=1)
                routes.append(route)
            samples.append(EnvironmentMetricSample(batch, ref, tuple(metrics), tuple(routes)))
            samples.append(EnvironmentMetricSample(batch, f"{kind}:{identifier + 100}", (), ()))
    return EnvironmentReadMatrixSnapshot(NOW, tuple(batches), tuple(samples))


class _Repository:
    def __init__(self, snapshot: EnvironmentReadMatrixSnapshot, *, drift: bool = False) -> None:
        self.snapshot, self.calls, self.drift = snapshot, 0, drift

    def active_environment_publications(self) -> tuple[dict[str, Any], ...]:
        """Return stable or switched publication identity without opening a database."""
        self.calls += 1
        if self.drift and self.calls > 1:
            return ({**self.snapshot.batches[0], "publish_version": "new-version"}, *self.snapshot.batches[1:])
        return self.snapshot.batches


class _API:
    def __init__(self, snapshot: EnvironmentReadMatrixSnapshot, *, corruption: str | None = None) -> None:
        self.snapshot, self.corruption, self.calls = snapshot, corruption, []

    def _sample(self, ref: str, market: str, profile: str) -> EnvironmentMetricSample:
        return next(sample for sample in self.snapshot.samples if sample.factor_ref == ref
                    and sample.batch["market_scope"] == market and sample.batch["route_profile_key"] == profile)

    def environment_metrics(self, ref: str, market: str, profile: str, **kwargs: Any) -> Any:
        """Return exact persisted fixture fields or deliberate empty/filter/version corruption."""
        sample = self._sample(ref, market, profile)
        self.calls.append(("metrics", sample.batch["id"], ref, kwargs))
        items = [dict(row) for row in sample.metrics if (not kwargs.get("evaluation_type") or row["evaluation_type"] == kwargs["evaluation_type"])
                 and (not kwargs.get("label_code") or row["label_code"] == kwargs["label_code"])]
        batch = {key: value.isoformat() if isinstance(value, datetime) else value
                 for key, value in sample.batch.items()}
        if self.corruption == "value" and sample.batch["id"] == 2 and ref.startswith("factor:") and items:
            items[0]["mean_ic"] = 99
        if self.corruption == "version":
            batch["publish_version"] = "foreign-version"
        if self.corruption == "empty" and not sample.metrics:
            items = [{"id": 999}]
        if self.corruption == "filter" and items:
            items[0]["label_code"] = "WIDE_RANGE"
            items[0]["evaluation_type"] = "cross_sectional" if kwargs.get("evaluation_type") == "time_series" else "time_series"
        return _response({"data": {"items": items, "batch": batch, "factor_ref": ref, "returned_count": len(items)}, "meta": {}})

    def environment_tags(self, ref: str, market: str, profile: str) -> Any:
        """Return full active route fields, including success with an empty route list."""
        sample = self._sample(ref, market, profile)
        self.calls.append(("tags", sample.batch["id"], ref, {}))
        publication = {key: value.isoformat() if isinstance(value, datetime) else value
                       for key, value in sample.batch.items()}
        routes = [dict(row) for row in sample.routes]
        if self.corruption == "route_label" and routes:
            routes[0]["label_code"] = "WIDE_RANGE"
        if self.corruption == "route_empty" and not routes:
            routes = [{"id": 999}]
        return _response({"data": {"items": routes, "publication": publication,
                                    "factor_ref": ref, "returned_count": len(sample.routes)}, "meta": {}})


@pytest.mark.parametrize("mode,scope,label", [("dimensions", None, None), ("dimensions", "time_series", None),
    ("dimensions", "cross_sectional", None), ("labels", None, "CHOPPY_UP"), ("implicit", None, None), ("tags", None, None)])
def test_matrix_covers_all_partitions_kinds_and_real_empty_outputs(mode: str, scope: str | None, label: str | None) -> None:
    snapshot = _matrix()
    api = _API(snapshot)
    check = Factor4ReadService(api).check_environment_output_matrix(snapshot, _Repository(snapshot), mode=mode,
                                                                 evaluation_type=scope, label_code=label)
    assert not check.issues and not check.evidence["blocked"]
    assert {call[1] for call in api.calls} == {1, 2}
    assert {call[2].split(":")[0] for call in api.calls} == {"factor", "sub_factor"}
    assert any(int(call[2].split(":")[1]) > 100 for call in api.calls)
    assert check.evidence["request_count"] == (16 if mode == "labels" else 8)
    if mode == "labels":
        assert {call[3]["evaluation_type"] for call in api.calls} == {"time_series", "cross_sectional"}
        assert all(call[3]["label_code"] == label for call in api.calls)
    if mode not in {"implicit", "tags"}:
        assert all(call[3]["batch_uid"] == f"batch-{call[1]}" for call in api.calls)


@pytest.mark.parametrize("corruption", ["value", "version", "empty", "filter"])
def test_matrix_rejects_nondefault_parent_data_version_and_nonempty_empty_shape(corruption: str) -> None:
    snapshot = _matrix()
    check = Factor4ReadService(_API(snapshot, corruption=corruption)).check_environment_output_matrix(
        snapshot, _Repository(snapshot), mode="labels", label_code="CHOPPY_UP")
    assert check.issues


@pytest.mark.parametrize("corruption", ["route_label", "route_empty"])
def test_tag_matrix_rejects_route_label_or_real_empty_output_corruption(corruption: str) -> None:
    snapshot = _matrix()
    check = Factor4ReadService(_API(snapshot, corruption=corruption)).check_environment_output_matrix(
        snapshot, _Repository(snapshot), mode="tags")
    assert check.issues


def test_missing_positive_shape_does_not_hide_other_partition_failures() -> None:
    snapshot = _matrix()
    samples = tuple(replace(sample, metrics=()) if sample.batch["id"] == 1 else sample for sample in snapshot.samples)
    snapshot = replace(snapshot, samples=samples)
    check = Factor4ReadService(_API(snapshot, corruption="value")).check_environment_output_matrix(snapshot, _Repository(snapshot), mode="dimensions")
    assert check.issues and check.evidence["blocked"]
    assert any("batch=2:factor:" in issue for issue in check.issues)
    assert any("batch=1" in missing for missing in check.evidence["blocked"])


def test_implicit_publication_switch_is_not_reported_as_matrix_pass() -> None:
    snapshot = _matrix()
    check = Factor4ReadService(_API(snapshot)).check_environment_output_matrix(snapshot, _Repository(snapshot, drift=True), mode="tags")
    assert not check.issues
    assert any("SNAPSHOT_DRIFT" in reason for reason in check.evidence["blocked"])


def test_partition_drift_excludes_unproven_comparison_but_preserves_stable_partition_failure() -> None:
    snapshot = _matrix()
    check = Factor4ReadService(_API(snapshot, corruption="version")).check_environment_output_matrix(
        snapshot, _Repository(snapshot, drift=True), mode="implicit")
    assert check.issues and check.evidence["blocked"]
    assert not any("batch=1:" in issue for issue in check.issues)
    assert any("batch=2:" in issue for issue in check.issues)
    assert check.checked_count > 0


def test_preexisting_pointer_drift_only_blocks_affected_partition() -> None:
    snapshot = _matrix()
    repository = _Repository(snapshot, drift=True)
    repository.calls = 1
    api = _API(snapshot, corruption="value")
    check = Factor4ReadService(api).check_environment_output_matrix(snapshot, repository, mode="implicit")
    assert {call[1] for call in api.calls} == {2}
    assert check.issues and check.evidence["blocked"]


def test_active_incremental_publication_does_not_require_finished_batch_status() -> None:
    snapshot = _matrix()
    batches = tuple({**batch, "status": "running"} for batch in snapshot.batches)
    samples = tuple(replace(sample, batch=batches[sample.batch["id"] - 1]) for sample in snapshot.samples)
    snapshot = replace(snapshot, batches=batches, samples=samples)
    check = Factor4ReadService(_API(snapshot)).check_environment_output_matrix(snapshot, _Repository(snapshot), mode="dimensions")
    assert not check.issues and not check.evidence["blocked"]


def test_dependency_block_does_not_stop_checks_in_other_partitions() -> None:
    class UnavailableFirstAPI(_API):
        def environment_metrics(self, ref: str, market: str, profile: str, **kwargs: Any) -> Any:
            """Block the first partition while retaining second-partition corruption."""
            if market == "all":
                raise ReadPrecondition("BLOCKED_DEPENDENCY: DEPENDENCY_UNAVAILABLE")
            return super().environment_metrics(ref, market, profile, **kwargs)

    snapshot = _matrix()
    check = Factor4ReadService(UnavailableFirstAPI(snapshot, corruption="value")).check_environment_output_matrix(
        snapshot, _Repository(snapshot), mode="dimensions")
    assert check.issues and check.evidence["blocked"]
    assert any("batch=2:factor:" in issue for issue in check.issues)


def test_explicit_metric_sample_reads_existing_entity_and_full_empty_rows() -> None:
    batch = _matrix().batches[0]
    db = _DB([{"id": 9}, batch], [[], []])
    sample = Factor4ReadRepository(db).metric_sample("factor", batch_uid=batch["batch_uid"], factor_ref="factor:9")
    assert sample is not None and sample.metrics == () and sample.routes == ()
    assert any("FROM factors WHERE id=%s" in sql and args == (9,) for sql, args in db.calls)
    assert any("WHERE batch_uid=%s" in sql and args == (batch["batch_uid"],) for sql, args in db.calls)
    assert db.calls[-1] == ("ROLLBACK", ())


def test_matrix_repository_discovery_preserves_no_data_partitions_and_read_only_transaction() -> None:
    batches = _matrix().batches
    # Both partitions intentionally have no result cells and no empty-shape entity:
    # their absence must remain visible to the matrix instead of removing partitions.
    db = _DB([{"as_of_time": NOW.replace(tzinfo=None)}, *([None] * 8)], [list(batches), [], []])
    snapshot = Factor4ReadRepository(db).environment_matrix_snapshot()
    assert snapshot.batches == batches and snapshot.samples == ()
    assert any("WHERE is_active=1" in sql for sql, _ in db.calls)
    assert sum("NOT EXISTS" in sql for sql, _ in db.calls) == 8
    assert all("factors_status" in sql and "current_status.status BETWEEN 0 AND 3" in sql
               for sql, _ in db.calls if "NOT EXISTS" in sql)
    assert db.calls[-1] == ("ROLLBACK", ())


def test_matrix_repository_deduplicates_shape_refs_then_loads_full_metric_and_route_rows() -> None:
    batch = _matrix().batches[0]
    metric_shapes = [{"eval_batch_id": 1, "factor_ref": "factor:7"},
                     {"eval_batch_id": 1, "factor_ref": "factor:7"}]
    route_shapes = [{"eval_batch_id": 1, "factor_ref": "factor:7"},
                    {"eval_batch_id": 1, "factor_ref": "sub_factor:8"}]
    db = _DB([{"as_of_time": NOW.replace(tzinfo=None)}, {"id": 7}, {"id": 7}, None, None],
             [[batch], metric_shapes, route_shapes, [{"id": 17}], [{"id": 27}], [{"id": 18}], [{"id": 28}]])
    snapshot = Factor4ReadRepository(db).environment_matrix_snapshot()
    assert [sample.factor_ref for sample in snapshot.samples] == ["factor:7", "sub_factor:8"]
    assert [sample.metrics[0]["id"] for sample in snapshot.samples] == [17, 18]
    assert [sample.routes[0]["id"] for sample in snapshot.samples] == [27, 28]
    assert sum("SELECT * FROM market_environment_factor_metric" in sql for sql, _ in db.calls) == 2
    assert db.calls[-1] == ("ROLLBACK", ())


def test_empty_entity_selection_excludes_latest_deleted_status_and_another_deleted_category() -> None:
    """Execute discovery predicates on in-memory SQL rows, never on an external database."""
    batch = _matrix().batches[0]
    recorder = _DB([{"as_of_time": NOW.replace(tzinfo=None)}, None, None, None, None], [[batch], [], []])
    Factor4ReadRepository(recorder).environment_matrix_snapshot()
    sql, parameters = next((sql, parameters) for sql, parameters in recorder.calls if "SELECT f.id FROM factors f" in sql)
    with sqlite3.connect(":memory:") as connection:
        connection.executescript("""
            CREATE TABLE factors (id INTEGER);
            CREATE TABLE factors_status (id INTEGER, factor_id INTEGER, coin_category TEXT,
                status INTEGER, is_sub_factor_id INTEGER, updated_at TEXT);
            CREATE TABLE market_environment_factor_metric (eval_batch_id INTEGER, factor_type TEXT, factor_id INTEGER);
            INSERT INTO factors VALUES (1),(2),(3),(4),(5);
            INSERT INTO factors_status VALUES
                (1,1,'all',2,0,'2026-01-01'), (2,1,'all',4,0,'2026-01-02'),
                (3,2,'all',2,0,'2026-01-01'), (4,2,'all',4,0,'2026-01-01'),
                (5,3,'all',2,0,'2026-01-01'), (6,3,'other',4,0,'2026-01-01'),
                (7,4,'all',4,0,'2026-01-01'), (8,4,'all',2,0,'2026-01-02'),
                (9,5,'all',2,1,'2026-01-01');
        """)
        # 1 was deleted later; 2 was deleted at the same time with a newer id;
        # 3 is deleted in another category; 4 was restored; 5 belongs to subfactors.
        assert connection.execute(sql.replace("%s", "?"), parameters).fetchall() == [(4,)]
