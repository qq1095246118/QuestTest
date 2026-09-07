"""Read-only catalog formula evidence and final-result reconciliation data."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections.abc import Iterator
from typing import Any

from db.client import DatabaseClient, DatabaseTransaction


@dataclass(frozen=True)
class FormulaCatalogSnapshot:
    """One consistent catalog/formula snapshot; payloads stay out of repr."""

    as_of: datetime
    details: tuple[dict[str, Any], ...] = field(repr=False)
    definitions: tuple[dict[str, Any], ...] = field(repr=False)
    evidence: tuple[dict[str, Any], ...] = field(repr=False)
    routes: tuple[dict[str, Any], ...] = field(repr=False)


@dataclass(frozen=True)
class FormulaResultSnapshot:
    """Exact completed formula rows and their persisted summaries/validity/values."""

    evidence: tuple[dict[str, Any], ...] = field(repr=False)
    summaries: tuple[dict[str, Any], ...] = field(repr=False)
    validity: tuple[dict[str, Any], ...] = field(repr=False)
    values: tuple[dict[str, Any], ...] = field(repr=False)


class Factor4FormulaRepository:
    """Read the formula catalog and exact-run final results without writes."""

    def __init__(self, client: DatabaseClient) -> None:
        """Accept a test-gated DB client; no connection or query is started."""
        self._client = client

    @contextmanager
    def _snapshot(self) -> Iterator[DatabaseTransaction]:
        try:
            with self._client.transaction() as transaction:
                transaction.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                transaction.execute("SET TRANSACTION READ ONLY")
                transaction.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
                try:
                    yield transaction
                finally:
                    transaction.execute("ROLLBACK")
        except Exception as exc:
            raise RuntimeError(f"Formula catalog read failed: {type(exc).__name__}") from None

    def catalog(self) -> FormulaCatalogSnapshot:
        """Read definitions, wrappers, evidence and all active eligible routes.

        Returns a coherent final-data snapshot. SQL/connection errors propagate
        as a credential-free RuntimeError; no raw price data or DB writes occur.
        """
        with self._snapshot() as tx:
            clock = tx.fetch_one("SELECT UTC_TIMESTAMP(6) AS as_of_time")
            details = tx.fetch_all("""
                SELECT id,factor_id,is_sub_factor_id,name,description,calc_logic,calc_function,
                       params,data_source_metadata,status,updated_at
                FROM factors_details ORDER BY id
            """)
            definitions = tx.fetch_all("""
                SELECT id,sub_factor_name,`window`,factor_bar_interval,formula_summary,metadata,updated_at
                FROM sub_factors ORDER BY id
            """)
            evidence = tx.fetch_all("""
                SELECT e.*,r.status AS run_status,r.completed_at AS run_completed_at
                FROM factor_ic_run_formula_evidence e
                LEFT JOIN factor_ic_runs r ON r.run_id=e.run_id
                ORDER BY e.id
            """)
            routes = tx.fetch_all("""
                SELECT r.id,r.factor_ref,r.factor_id,r.factor_type,r.metric_id,r.eval_batch_id,
                       r.label_code,r.is_active,r.is_eligible,m.metric_payload
                FROM market_environment_factor_route r
                LEFT JOIN market_environment_factor_metric m ON m.id=r.metric_id
                WHERE r.is_active=1 AND r.is_eligible=1 ORDER BY r.id
            """)
        if not clock or not isinstance(clock.get("as_of_time"), datetime):
            raise RuntimeError("Formula catalog database clock is missing")
        return FormulaCatalogSnapshot(clock["as_of_time"].replace(tzinfo=timezone.utc),
                                      tuple(details), tuple(definitions), tuple(evidence), tuple(routes))

    def factor_results(self, factor_id: int) -> FormulaResultSnapshot:
        """Read all completed formula evidence and final summaries for a sub-factor.

        The latest persisted value is selected independently, so a value whose
        Run has no formula is detectable. A positive ID is required (ValueError);
        database errors become credential-free RuntimeError. No write occurs.
        """
        if isinstance(factor_id, bool) or not isinstance(factor_id, int) or factor_id <= 0:
            raise ValueError("factor_id must be a positive integer")
        with self._snapshot() as tx:
            evidence = tx.fetch_all("""
                SELECT e.*,r.status AS run_status,r.completed_at AS run_completed_at
                FROM factor_ic_run_formula_evidence e
                JOIN factor_ic_runs r ON r.run_id=e.run_id
                WHERE e.factor_id=%s AND e.is_sub_factor_id=1 AND r.status='completed'
                ORDER BY r.completed_at DESC,e.recorded_at DESC,e.id DESC
            """, (factor_id,))
            summaries = tx.fetch_all("""
                SELECT s.*,r.completed_at AS run_completed_at
                FROM factor_ic_summary_metrics s JOIN factor_ic_runs r ON r.run_id=s.run_id
                WHERE s.factor_id=%s AND s.is_sub_factor_id=1 AND r.status='completed'
                ORDER BY r.completed_at DESC,s.id DESC
            """, (factor_id,))
            validity = tx.fetch_all("""
                SELECT v.*,r.completed_at AS run_completed_at
                FROM factor_validity_status v JOIN factor_ic_runs r ON r.run_id=v.run_id
                WHERE v.factor_id=%s AND v.is_sub_factor_id=1 AND r.status='completed'
                ORDER BY r.completed_at DESC,v.id DESC
            """, (factor_id,))
            values = tx.fetch_all("""
                SELECT id,run_id,factor_id,is_sub_factor_id,factor_bar_interval,factor_window_bars,
                       symbol,as_of_time,factor_value,adjusted_factor_value,weighting_method,created_at
                FROM factor_value_slice_metrics WHERE factor_id=%s AND is_sub_factor_id=1
                ORDER BY created_at DESC,id DESC LIMIT 1
            """, (factor_id,))
        return FormulaResultSnapshot(tuple(evidence), tuple(summaries), tuple(validity), tuple(values))
