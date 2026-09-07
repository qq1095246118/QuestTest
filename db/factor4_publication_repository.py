"""Read-only publication history and final routes used by recommendation cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from db.client import DatabaseClient


@dataclass(frozen=True)
class PublicationHistory:
    """One consistent database view; payloads are excluded from diagnostics."""

    as_of: datetime
    batches: tuple[dict[str, Any], ...] = field(repr=False)
    routes: tuple[dict[str, Any], ...] = field(repr=False)


class Factor4PublicationRepository:
    """Access final publication records without starting evaluation or publication."""

    def __init__(self, client: DatabaseClient) -> None:
        """Accept a gated test DB client; perform no I/O and return nothing."""
        self._client = client

    def history(self) -> PublicationHistory:
        """Return published history and routes atomically; fail safely on DB errors.

        Superseded batches remain in the result so PIT expectations do not use today's
        active flag to decide whether a historical publication was visible.
        """
        try:
            with self._client.transaction() as tx:
                tx.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                tx.execute("SET TRANSACTION READ ONLY")
                tx.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
                try:
                    clock = tx.fetch_one("SELECT UTC_TIMESTAMP(6) AS as_of_time")
                    batches = tx.fetch_all("""
                        SELECT id, batch_uid, market_scope, route_profile_key, status,
                               publish_status, published_at, publication_uid,
                               publish_version, is_active, score_rule_version
                        FROM market_environment_eval_batch
                        WHERE publish_status='published' AND published_at IS NOT NULL
                        ORDER BY published_at, id
                    """)
                    routes = tx.fetch_all("""
                        SELECT r.* FROM market_environment_factor_route r
                        JOIN market_environment_eval_batch b ON b.id=r.eval_batch_id
                        WHERE b.publish_status='published' AND b.published_at IS NOT NULL
                        ORDER BY r.eval_batch_id, r.label_code, r.rank_no, r.id
                    """)
                finally:
                    tx.execute("ROLLBACK")
            if not clock or not isinstance(clock.get("as_of_time"), datetime):
                raise ValueError("database clock missing")
            return PublicationHistory(clock["as_of_time"].replace(tzinfo=timezone.utc),
                                      tuple(batches), tuple(routes))
        except Exception as error:
            raise RuntimeError(f"Publication DB read failed: {type(error).__name__}") from None
