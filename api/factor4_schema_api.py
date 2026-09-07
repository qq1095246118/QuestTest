"""Approved field and raw-schema read endpoints."""

from __future__ import annotations

from collections.abc import Sequence

from api.factor_data_mcp_api import FactorDataMCPAPI, MCPResponse


class Factor4SchemaAPI:
    """Expose schema selectors without business validation."""

    def __init__(self, mcp: FactorDataMCPAPI) -> None:
        """Accept the test-gated transport; no I/O or exceptions."""
        self.mcp = mcp

    def fields(self, *, version: str | None = None, names: Sequence[str] | None = None) -> MCPResponse:
        """Read all or selected fields at a version; transport errors propagate."""
        arguments: dict[str, object] = {}
        if version is not None:
            arguments["schema_version"] = version
        if names is not None:
            arguments["field_names"] = list(names)
        return self.mcp.call_tool("schema_get_factor_fields", arguments)

    def raw(self, *, version: str | None = None) -> MCPResponse:
        """Read raw mappings, resolutions and replay fixtures; transport errors propagate."""
        return self.mcp.call_tool("schema_get_raw_data", {} if version is None else {"schema_version": version})
