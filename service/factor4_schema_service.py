"""Compare approved schema responses with persisted versioned entities."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from api.factor4_schema_api import Factor4SchemaAPI
from api.factor_data_mcp_api import MCPJSONRPCError
from db.factor4_schema_repository import ApprovedSchemaSnapshot
from service.factor4_read_service import ReadCheck, ReadContractError, ReadPrecondition, read_tool_body, read_tool_page


def _decoded(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in {"[", "{"}:
        try:
            return _decoded(json.loads(value))
        except ValueError:
            return value
    if isinstance(value, dict):
        return {key: _decoded(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decoded(item) for item in value]
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=ZoneInfo("Asia/Shanghai"))).astimezone(timezone.utc)
    if isinstance(value, str) and "T" in value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo:
                return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return value


def _rows(data: Mapping[str, Any], name: str) -> tuple[dict[str, Any], ...]:
    rows = data.get(name)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ReadContractError(f"schema:{name}:object_list_required")
    return tuple(rows)


def _compare_rows(actual: tuple[dict[str, Any], ...], expected: tuple[dict[str, Any], ...], key: str, prefix: str) -> list[str]:
    issues: list[str] = []
    actual_keys, expected_keys = [str(row.get(key)) for row in actual], [str(row.get(key)) for row in expected]
    if len(set(actual_keys)) != len(actual_keys) or set(actual_keys) != set(expected_keys):
        issues.append(prefix + ":membership")
    by_key = {str(row.get(key)): row for row in expected}
    for row in actual:
        source = by_key.get(str(row.get(key)))
        if source is None:
            continue
        required = {"source_dataset", "source_field", "data_type", "field_class", "expression",
                    "fixture_json", "expected_json", "fixture_hash", "input_fields_json"}
        for field_name in sorted(required & set(source) - set(row)):
            issues.append(f"{prefix}:{row.get(key)}:missing_field={field_name}")
        # API projection fields are compared, including explicit nulls. Server-only
        # derived metadata is not treated as a persisted DB column.
        for field_name, value in row.items():
            expected_value = source.get(field_name)
            # Schema applicability instants use UTC storage; approval/audit
            # DATETIME values retain the application's Shanghai wall clock.
            if field_name in {"effective_from", "effective_to"} and isinstance(expected_value, datetime) and expected_value.tzinfo is None:
                expected_value = expected_value.replace(tzinfo=timezone.utc)
            if field_name in source and _decoded(value) != _decoded(expected_value):
                issues.append(f"{prefix}:{row.get(key)}:field={field_name}")
    return issues


class Factor4SchemaService:
    """Reconcile full/default/explicit schema and independently selected fields."""

    def __init__(self, api: Factor4SchemaAPI) -> None:
        """Accept endpoint adapter; no network access or constructor errors."""
        self.api = api

    def check_approved(self, snapshot: ApprovedSchemaSnapshot, *, raw: bool, explicit: bool) -> ReadCheck:
        """Compare version, complete members and exposed persisted fields; I/O errors propagate."""
        version = snapshot.version if explicit else None
        data = read_tool_page(self.api.raw(version=version) if raw else self.api.fields(version=version)).data
        issues = [] if data.get("schema_version") == snapshot.version else ["schema:approved_version"]
        if raw:
            for name, expected, key in (("mappings", snapshot.mappings, "field_name"), ("field_resolutions", snapshot.resolutions, "field_name"), ("replay_cases", snapshot.replays, "case_key")):
                actual = _rows(data, name)
                issues.extend(_compare_rows(actual, expected, key, "schema:" + name))
            count = len(snapshot.mappings) + len(snapshot.resolutions) + len(snapshot.replays)
        else:
            actual = _rows(data, "fields")
            expected = {str(row["field_name"]): row for row in (*snapshot.mappings, *snapshot.resolutions)}
            issues.extend(_compare_rows(actual, tuple(expected.values()), "field_name", "schema:fields"))
            count = len(expected)
        if not count:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: approved schema has no mappings")
        return ReadCheck(count, tuple(issues))

    def check_selected(self, snapshot: ApprovedSchemaSnapshot, selector: str) -> ReadCheck:
        """Check only requested fields, dependency closure and three immutable version replays.

        Input selector is vwap, close or discovered. Missing actual approved field blocks;
        unknown selector raises ValueError, transport/contract exceptions propagate.
        """
        if selector not in {"vwap", "close", "discovered"}:
            raise ValueError("unsupported field selector")
        known = {str(row["field_name"]): row for row in (*snapshot.mappings, *snapshot.resolutions)}
        selected = next(iter(known), "") if selector == "discovered" else selector
        if selected not in known:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: requested approved field absent")
        pages = [read_tool_page(self.api.fields(version=snapshot.version, names=[selected])).data for _ in range(3)]
        issues: list[str] = []
        for data in pages:
            rows = _rows(data, "fields")
            issues.extend(_compare_rows(rows, (known[selected],), "field_name", "schema:selected"))
            if data.get("schema_version") != snapshot.version:
                issues.append("schema:selected_version")
            if len(rows) != 1:
                continue
            row = rows[0]
            dependencies = _decoded(row.get("dependency_fields", row.get("dependency_fields_json", [])))
            expected_dependencies = _decoded(known[selected].get("dependency_fields_json", []))
            if not isinstance(dependencies, list) or set(dependencies) != set(expected_dependencies or []):
                issues.append("schema:selected_dependencies")
            elif not set(dependencies) <= set(known):
                issues.append("schema:unapproved_dependencies")
            if selector == "vwap" and set(dependencies or []) != {"high", "low", "close", "volume"}:
                issues.append("schema:vwap_raw_dependencies")
        if any(data != pages[0] for data in pages[1:]):
            issues.append("schema:selected_replay_changed")
        return ReadCheck(len(pages), tuple(dict.fromkeys(issues)))

    def check_invalid_selector(self, variant: str) -> ReadCheck:
        """Deferred invalid-selector checks; structured rejection or exact unresolved result only.

        Supported variants field, fields_version, raw_version, extra. Unexpected variants
        raise ValueError; network failures remain errors rather than expected rejections.
        """
        missing = "questtest-missing-" + uuid4().hex
        try:
            if variant == "field":
                response = self.api.fields(names=[missing])
            elif variant == "fields_version":
                response = self.api.fields(version=missing)
            elif variant == "raw_version":
                response = self.api.raw(version=missing)
            elif variant == "extra":
                response = self.api.mcp.call_tool("schema_get_raw_data", {"unexpected": True})
            else:
                raise ValueError("unsupported schema invalid selector")
        except MCPJSONRPCError as exc:
            if exc.code not in {-32601, -32602}:
                raise
            return ReadCheck(1, ())
        if response.is_tool_error:
            body = read_tool_body(response)
            code = body.get("error", {}).get("code")
            if code in {"FIELD_NOT_APPROVED", "SCHEMA_NOT_FOUND", "SCHEMA_VERSION_NOT_FOUND", "VALIDATION_ERROR", "INVALID_ARGUMENT", "INVALID_ARGUMENTS", "INVALID_PARAMS", "NOT_FOUND"}:
                return ReadCheck(1, ())
            read_tool_page(response)
        page = read_tool_page(response)
        if variant == "field":
            rows = _rows(page.data, "fields")
            valid = (len(rows) == 1 and rows[0].get("field_name") == missing
                     and rows[0].get("resolution_status") == "unresolved"
                     and "FIELD_NOT_APPROVED" in (rows[0].get("unresolved_reasons") or [])
                     and not rows[0].get("final_raw_dependencies"))
        elif variant == "extra":
            valid = False
        else:
            valid = not any(page.data.get(key) for key in ("fields", "mappings", "field_resolutions", "replay_cases"))
        return ReadCheck(1, () if valid else ("schema:invalid_selector_fell_back",))
