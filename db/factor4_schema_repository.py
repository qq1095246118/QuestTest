"""Read-only approved schema entities for independent MCP reconciliation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from db.client import DatabaseClient


@dataclass(frozen=True)
class ApprovedSchemaSnapshot:
    """One approved version with all persisted mapping/resolution/replay entities."""

    version: str
    mappings: tuple[dict[str, Any], ...] = field(repr=False)
    resolutions: tuple[dict[str, Any], ...] = field(repr=False)
    replays: tuple[dict[str, Any], ...] = field(repr=False)


class Factor4SchemaRepository:
    """Read schema data only; never execute the stored expressions or replay code."""

    def __init__(self, client: DatabaseClient) -> None:
        """Accept a test-gated DB client; no query or exception at construction."""
        self._client = client

    def approved(self, version: str | None = None) -> ApprovedSchemaSnapshot | None:
        """Return exact approved version, or newest only when version is omitted.

        Read full children in one read-only snapshot. Missing data returns None;
        blank versions raise ValueError, DB failures become credential-free RuntimeError.
        Explicit historical versions never fall back to the current schema.
        """
        if version is not None and (not isinstance(version, str) or not version.strip()):
            raise ValueError("schema version must be a nonblank string")
        try:
            with self._client.transaction() as tx:
                tx.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                tx.execute("SET TRANSACTION READ ONLY")
                tx.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
                try:
                    if version is None:
                        row = tx.fetch_one("SELECT schema_version FROM raw_data_schema_version WHERE status='approved' ORDER BY approved_at DESC,id DESC LIMIT 1")
                    else:
                        row = tx.fetch_one(
                            "SELECT schema_version FROM raw_data_schema_version WHERE status='approved' AND schema_version=%s",
                            (version,),
                        )
                    if row is None:
                        return None
                    version = str(row["schema_version"])
                    mappings = tx.fetch_all("SELECT * FROM raw_data_field_mapping WHERE schema_version=%s ORDER BY field_name", (version,))
                    resolutions = tx.fetch_all("SELECT * FROM factor_field_resolution_mapping WHERE schema_version=%s ORDER BY field_name", (version,))
                    replays = tx.fetch_all("SELECT * FROM factor_replay_case WHERE schema_version=%s ORDER BY id", (version,))
                    return ApprovedSchemaSnapshot(version, tuple(mappings), tuple(resolutions), tuple(replays))
                finally:
                    tx.execute("ROLLBACK")
        except Exception as exc:
            raise RuntimeError(f"Approved schema read failed: {type(exc).__name__}") from None
