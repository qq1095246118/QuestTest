"""Formula/raw-schema endpoint semantics over the shared MCP transport."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from api.factor_data_mcp_api import FactorDataMCPAPI, MCPResponse


class Factor4FormulaAPI:
    """Expose exact formula identities, schema and final-data reads."""

    def __init__(self, mcp: FactorDataMCPAPI) -> None:
        """Accept a test-gated MCP client without network activity."""
        self.mcp = mcp

    def raw_schema(self) -> MCPResponse:
        """Read the global approved raw-field schema; transport errors propagate."""
        return self.mcp.call_tool("schema_get_raw_data", {})

    def detail(self, factor_ref: str, level: str) -> MCPResponse:
        """Read one explicit projection level; protocol/transport errors propagate."""
        return self.mcp.get_factor_detail(factor_ref, detail_level=level)

    def formula(self, row: Mapping[str, Any], *, window: str | None = None,
                as_of: str | None = None) -> MCPResponse:
        """Read the exact immutable identity in row; optional window overrides one dimension.

        Optional as_of fixes visibility to the caller's original recommendation instant.
        Missing identity keys raise KeyError before network I/O. Server/transport
        failures are not caught; this method never chooses another Run.
        """
        return self.mcp.get_formula(
            f"{'sub_factor' if row['is_sub_factor_id'] else 'factor'}:{row['factor_id']}",
            run_id=row["run_id"], interval=row["factor_bar_interval"],
            factor_window_bars=window or row["factor_window_bars"],
            return_bar_interval=row["return_bar_interval"],
            forward_return_bars=int(row["forward_return_bars"]),
            calculation_mode=row["calculation_mode"],
            **({"as_of": as_of} if as_of is not None else {}),
        )
