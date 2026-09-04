from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MCP_URL = "https://test-factor-frontend.questvector.ai/mcp/factor-data"
SOURCE_DIR = Path("reports/factor4-rerun/20260902T091825Z-formula")
OUTPUT_DIR = Path("reports/factor4-rerun/20260902T094800Z-field-runtime")


def _call_mcp(token: str, request_id: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call one read-only MCP tool and return transport and parsed business envelopes."""
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    request = urllib.request.Request(
        MCP_URL,
        data=json.dumps(payload, separators=(",", ":")).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "curl/8.7.1",
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            raw = response.read()
            http_status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        http_status = exc.code
    elapsed = round(time.monotonic() - started, 3)
    envelope = json.loads(raw)
    result = envelope.get("result") or {}
    structured = result.get("structuredContent")
    if structured is None and result.get("content"):
        structured = json.loads(result["content"][0]["text"])
    return {
        "request": payload,
        "http_status": http_status,
        "elapsed_seconds": elapsed,
        "envelope": envelope,
        "business": structured,
    }


def _summarize_detail(item: dict[str, Any], unresolved: dict[str, Any]) -> dict[str, Any]:
    """Return a credential-free summary of one factor detail and its field resolution state."""
    data = item.get("data") or {}
    metadata = data.get("metadata") or {}
    source = data.get("data_source_metadata") or {}
    datasets = source.get("datasets") or []
    return {
        "factor_ref": unresolved["factor_ref"],
        "name": unresolved["name"],
        "detail_success": item.get("success"),
        "formula_available": data.get("formula_available"),
        "formula": data.get("calc_logic"),
        "declared_fields": unresolved["declared_fields"],
        "canonical_resolution": [
            {
                "field_name": row.get("field_name"),
                "resolution_status": row.get("resolution_status"),
                "unresolved_reasons": row.get("unresolved_reasons") or [],
                "final_raw_dependencies": row.get("final_raw_dependencies") or [],
            }
            for row in metadata.get("field_resolution") or []
        ],
        "runtime_source_resolution": {
            "resolved_raw_fields": source.get("resolved_raw_fields"),
            "unresolved_fields": source.get("unresolved_fields"),
            "datasets": [
                {
                    "dataset_key": row.get("dataset_key"),
                    "provider": row.get("provider"),
                    "endpoint": row.get("endpoint"),
                    "method": row.get("method"),
                    "fields": row.get("fields") or [],
                    "source_interval": row.get("source_interval"),
                    "target_interval": row.get("target_interval"),
                }
                for row in datasets
            ],
            "derived_fields": source.get("derived_fields"),
        },
        "definition_contract_status": metadata.get("definition_contract_status"),
        "formula_metadata_complete": metadata.get("formula_metadata_complete"),
        "formula_metadata_warnings": metadata.get("formula_metadata_warnings"),
        "detail_updated_at": data.get("detail_updated_at"),
    }


def main() -> None:
    """Capture current MCP definition and approved raw-schema evidence for the 20 candidates."""
    token = os.environ.get("FIELD_RUNTIME_MCP_TOKEN")
    if not token:
        raise SystemExit("FIELD_RUNTIME_MCP_TOKEN is required")
    unresolved_payload = json.loads((SOURCE_DIR / "field-resolution.json").read_text())
    unresolved = unresolved_payload["unresolved"]
    refs = [row["factor_ref"] for row in unresolved]
    details = _call_mcp(
        token,
        "field-runtime-details",
        "factor_get_details_batch",
        {"factor_refs": refs, "detail_level": "executable"},
    )
    raw_schema = _call_mcp(token, "field-runtime-raw-schema", "schema_get_raw_data", {})
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "mcp-details-request.json").write_text(
        json.dumps(details["request"], indent=2, ensure_ascii=False) + "\n"
    )
    (OUTPUT_DIR / "mcp-details-response.json").write_text(
        json.dumps(details["envelope"], indent=2, ensure_ascii=False) + "\n"
    )
    (OUTPUT_DIR / "raw-schema-request.json").write_text(
        json.dumps(raw_schema["request"], indent=2, ensure_ascii=False) + "\n"
    )
    (OUTPUT_DIR / "raw-schema-response.json").write_text(
        json.dumps(raw_schema["envelope"], indent=2, ensure_ascii=False) + "\n"
    )
    items = ((details.get("business") or {}).get("data") or {}).get("items") or []
    by_ref = {row.get("factor_ref"): row for row in items}
    raw_data = (raw_schema.get("business") or {}).get("data") or {}
    approved = {row.get("field_name") for row in raw_data.get("mappings") or []}
    approved.update(row.get("field_name") for row in raw_data.get("field_resolutions") or [])
    summary = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "environment": "test",
        "mcp_host": "test-factor-frontend.questvector.ai",
        "read_only": True,
        "candidate_count": len(unresolved),
        "details_http_status": details["http_status"],
        "details_is_error": (details.get("envelope") or {}).get("result", {}).get("isError"),
        "details_elapsed_seconds": details["elapsed_seconds"],
        "raw_schema_http_status": raw_schema["http_status"],
        "raw_schema_is_error": (raw_schema.get("envelope") or {}).get("result", {}).get("isError"),
        "raw_schema_elapsed_seconds": raw_schema["elapsed_seconds"],
        "raw_schema_identity": {
            "schema_version": raw_data.get("schema_version"),
            "schema_hash": raw_data.get("schema_hash"),
            "status": raw_data.get("status"),
            "contract_scope": (raw_data.get("contract") or {}).get("contract_scope"),
            "approved_field_count": len(approved),
        },
        "items": [
            _summarize_detail(by_ref.get(row["factor_ref"], {}), row) for row in unresolved
        ],
    }
    (OUTPUT_DIR / "mcp-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    )
    print(json.dumps({
        "output_dir": str(OUTPUT_DIR),
        "candidate_count": len(unresolved),
        "returned_detail_count": len(items),
        "details_http_status": details["http_status"],
        "raw_schema_http_status": raw_schema["http_status"],
    }))


if __name__ == "__main__":
    main()
