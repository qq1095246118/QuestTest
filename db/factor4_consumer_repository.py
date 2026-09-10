"""Read final evidence for a factor selected through the public MCP workflow."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from db.factor4_read_repository import SUMMARY_KEYS, Factor4ReadRepository, SummarySample, ValiditySample


@dataclass(frozen=True)
class SelectedResultEvidence:
    """Exact persisted formula and validity rows; no calculated input is required."""

    formulas: tuple[dict[str, Any], ...] = field(repr=False)
    validities: tuple[ValiditySample, ...] = field(repr=False)


class Factor4ConsumerRepository(Factor4ReadRepository):
    """Reuse read-only transactions for evidence attached to a selected summary."""

    def initial_scope(self, shape: str) -> SummarySample | None:
        """Find initial user filters without minimum population or metric-value gates.

        Accepts TS symbol, TS aggregate or CS aggregate; returns a seed with only
        scope and fixed DB time, or None when that shape has no completed result.
        Invalid shapes raise ValueError; sanitized database errors propagate.
        """
        shapes = {"ts_symbol": ("time_series", "COALESCE(m.symbol,'')<>''"),
                  "ts_aggregate": ("time_series", "COALESCE(m.symbol,'')=''"),
                  "cs_aggregate": ("cross_sectional", "COALESCE(m.symbol,'')=''")}
        if shape not in shapes:
            raise ValueError("unsupported consumer shape")
        scope, symbol = shapes[shape]
        with self._snapshot() as tx:
            as_of = self._clock(tx)
            cutoff = as_of.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
            row = tx.fetch_one(f"""SELECT {', '.join('m.' + key for key in SUMMARY_KEYS)}
                FROM factor_ic_summary_metrics m JOIN factor_ic_runs r ON r.run_id=m.run_id
                WHERE m.is_sub_factor_id=1 AND m.ic_scope=%s AND {symbol}
                  AND r.status='completed' AND r.completed_at<=%s
                ORDER BY r.completed_at DESC,m.id DESC LIMIT 1""", (scope, cutoff))
        return SummarySample("sub_factor", as_of, row, (), ()) if row else None

    def selected_run_summaries(self, summary: Mapping[str, Any], as_of: datetime) -> tuple[dict[str, Any], ...]:
        """Read every persisted period in the selected exact Run and full scope.

        No maximum period or updated_at is imposed on the multi-row metrics output.
        A naive instant raises ValueError, absent rows return (), and missing keys
        or sanitized DB failures propagate. This method performs no writes.
        """
        if as_of.tzinfo is None:
            raise ValueError("selected summaries require aware as_of")
        keys = ("run_id", "factor_id", "is_sub_factor_id", *SUMMARY_KEYS)
        predicate = " AND ".join(f"m.{key} <=> %s" for key in keys)
        cutoff = as_of.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        with self._snapshot() as tx:
            rows = tx.fetch_all(f"""SELECT m.* FROM factor_ic_summary_metrics m
                JOIN factor_ic_runs r ON r.run_id=m.run_id AND r.status='completed'
                WHERE {predicate} AND r.completed_at<=%s ORDER BY m.period_start,m.period_end,m.id""",
                (*(summary[key] for key in keys), cutoff))
        return tuple(rows)

    def selected_result_evidence(self, summary: Mapping[str, Any], as_of: datetime) -> SelectedResultEvidence:
        """Read evidence for the exact persisted summary, Run and aware query instant.

        Does not choose a different factor, symbol, window or Run when evidence is
        absent. Empty tuples retain the missing-data distinction. Invalid scope or
        naive as_of raises ValueError; missing keys and sanitized DB errors propagate.
        """
        if as_of.tzinfo is None or summary["ic_scope"] not in {"time_series", "cross_sectional"}:
            raise ValueError("selected evidence requires aware as_of and TS/CS scope")
        cutoff = as_of.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        scope = "ts" if summary["ic_scope"] == "time_series" else "cs"
        prefix = summary["ic_scope"]
        keys = ("run_id", "factor_id", "is_sub_factor_id", "calculation_mode",
                "factor_bar_interval", "factor_window_bars", "return_bar_interval", "forward_return_bars")
        predicate = " AND ".join(f"{key} <=> %s" for key in keys)
        with self._snapshot() as tx:
            formulas = tx.fetch_all(f"""SELECT * FROM factor_ic_run_formula_evidence
                WHERE {predicate} AND recorded_at<=%s ORDER BY id""",
                (*(summary[key] for key in keys), cutoff))
            rows = tx.fetch_all(f"""SELECT * FROM factor_validity_status
                WHERE run_id=%s AND factor_id=%s AND is_sub_factor_id=%s
                  AND {prefix}_summary_id=%s AND updated_at<=%s ORDER BY id""",
                (summary["run_id"], summary["factor_id"], summary["is_sub_factor_id"], summary["id"], cutoff))
        return SelectedResultEvidence(tuple(formulas), tuple(
            ValiditySample(row, {scope: dict(summary)}) for row in rows))
