#!/usr/bin/env python3
"""Adjudicate CALC-510 fixed-horizon candidates against MCP and the test DB.

This is a one-shot, read-only probe. It distinguishes a formula's actual raw
dependency span from a declared evaluation window and persists only sanitized
evidence under ``reports/factor4-resume``.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pymysql
import yaml


ROOT = Path(__file__).resolve().parents[1]
MCP_URL = "https://test-factor-frontend.questvector.ai/mcp/factor-data"
TOKEN_ENV = "FACTOR4_MCP_TOKEN"
TARGET_IDS = (180, 181, 183, 274, 276)
SHANGHAI = ZoneInfo("Asia/Shanghai")
SENSITIVE_KEY = re.compile(r"authorization|token|password|secret|api[_-]?key", re.I)

# These are examples of the formula shape implied by each factor family's own
# horizon convention. A different implementation is acceptable if its actual
# dependency horizon equals the declared horizon and preserves the definition.
EXPECTED_SHAPES = {
    180: "funding_rate.diff(12).diff(12)",
    181: "funding_rate.diff(24).diff(24)",
    183: "funding_rate.diff(36).diff(36)",
    274: "long_short_ratio.pct_change(48)",
    276: "long_short_ratio.pct_change(72)",
}


def json_default(value: Any) -> Any:
    """Return a JSON-safe representation or raise ``TypeError``.

    Args:
        value: A database-native or standard Python value.

    Returns:
        An ISO timestamp, decimal string, or decoded byte string.

    Raises:
        TypeError: If ``value`` has no supported representation.
    """

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    raise TypeError(type(value).__name__)


def redact(value: Any) -> Any:
    """Recursively redact credential-shaped fields and token strings.

    Args:
        value: Any JSON-compatible evidence value.

    Returns:
        A recursively sanitized value.
    """

    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if SENSITIVE_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str) and "naf_mcp_" in value:
        return "<redacted>"
    return value


def write_json(path: Path, value: Any) -> None:
    """Write sanitized JSON evidence.

    Args:
        path: Destination file.
        value: Evidence object to serialize.

    Returns:
        None.
    """

    path.write_text(
        json.dumps(redact(value), ensure_ascii=False, indent=2, default=json_default) + "\n",
        encoding="utf-8",
    )


def decode_json(value: Any) -> dict[str, Any]:
    """Decode a nullable JSON object from a database field.

    Args:
        value: Mapping, text, bytes, or null.

    Returns:
        The decoded mapping, or an empty mapping when decoding is impossible.
    """

    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def numeric_window(value: Any) -> int | None:
    """Extract the first integer bar count from a window value.

    Args:
        value: Numeric or labeled window such as ``48H``.

    Returns:
        A positive integer bar count, or ``None`` when absent.
    """

    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else None


def _constant_int(node: ast.AST) -> int | None:
    """Read an integer literal from one AST node.

    Args:
        node: Formula AST node.

    Returns:
        The integer value, or ``None`` for a non-integer expression.
    """

    if isinstance(node, ast.Constant) and isinstance(node.value, int) and node.value >= 0:
        return node.value
    return None


def _offsets(node: ast.AST) -> set[int]:
    """Calculate raw-series offsets required by a supported formula AST.

    Args:
        node: A formula expression node.

    Returns:
        Non-negative raw input offsets relative to the current bar.

    Raises:
        ValueError: If a temporal call uses a non-literal or unsupported window.
    """

    if isinstance(node, ast.Expression):
        return _offsets(node.body)
    if isinstance(node, ast.Name):
        return {0}
    if isinstance(node, ast.Constant):
        return set()
    if isinstance(node, ast.UnaryOp):
        return _offsets(node.operand)
    if isinstance(node, ast.BinOp):
        return _offsets(node.left) | _offsets(node.right)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        base = _offsets(node.func.value)
        operation = node.func.attr
        if operation in {"diff", "pct_change"}:
            period = _constant_int(node.args[0]) if node.args else 1
            if period is None:
                raise ValueError(f"{operation} period is not a non-negative integer literal")
            return base | {offset + period for offset in base}
        if operation == "shift":
            period = _constant_int(node.args[0]) if node.args else 1
            if period is None:
                raise ValueError("shift period is not a non-negative integer literal")
            return {offset + period for offset in base}
        if operation == "rolling":
            period = _constant_int(node.args[0]) if node.args else None
            if period is None or period < 1:
                raise ValueError("rolling window is not a positive integer literal")
            return {offset + lag for offset in base for lag in range(period)}
        return base | {offset for argument in node.args for offset in _offsets(argument)}
    offsets: set[int] = set()
    for child in ast.iter_child_nodes(node):
        offsets |= _offsets(child)
    return offsets


def formula_offsets(expression: str) -> list[int]:
    """Parse a formula and return its exact raw input offsets.

    Args:
        expression: Python-style factor formula.

    Returns:
        Sorted raw input offsets in bars.

    Raises:
        SyntaxError: If ``expression`` is not valid Python syntax.
        ValueError: If temporal arguments cannot be statically resolved.
    """

    return sorted(_offsets(ast.parse(expression, mode="eval")))


def parse_mcp(raw: bytes, content_type: str) -> dict[str, Any] | None:
    """Parse one JSON or single-event SSE MCP response.

    Args:
        raw: Raw HTTP response bytes.
        content_type: HTTP content type.

    Returns:
        The JSON-RPC envelope, or ``None`` for an empty response.

    Raises:
        ValueError: If SSE contains anything other than one JSON object event.
        json.JSONDecodeError: If an event is malformed JSON.
    """

    if not raw:
        return None
    text = raw.decode("utf-8", "replace")
    if "text/event-stream" not in content_type.lower():
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    events: list[dict[str, Any]] = []
    for block in re.split(r"\r?\n\r?\n", text):
        lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
        if lines:
            parsed = json.loads("\n".join(lines))
            if isinstance(parsed, dict):
                events.append(parsed)
    if len(events) != 1:
        raise ValueError(f"expected one MCP data event, got {len(events)}")
    return events[0]


def business_data(envelope: dict[str, Any] | None) -> tuple[dict[str, Any], str | None]:
    """Extract structured MCP data and a business error code.

    Args:
        envelope: JSON-RPC response envelope.

    Returns:
        A ``(data, error_code)`` tuple. ``data`` is empty when unavailable.
    """

    result = envelope.get("result") if isinstance(envelope, dict) else None
    structured = result.get("structuredContent") if isinstance(result, dict) else None
    if not isinstance(structured, dict) and isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list) and content and isinstance(content[0], dict):
            try:
                candidate = json.loads(str(content[0].get("text") or ""))
            except json.JSONDecodeError:
                candidate = {}
            structured = candidate if isinstance(candidate, dict) else {}
    structured = structured if isinstance(structured, dict) else {}
    error = structured.get("error")
    error_code = str(error.get("code")) if isinstance(error, dict) and error.get("code") else None
    data = structured.get("data")
    return (data if isinstance(data, dict) else {}, error_code)


class McpClient:
    """Minimal MCP client that saves sanitized request and response bodies."""

    def __init__(self, token: str, output: Path) -> None:
        """Create a client for one evidence directory.

        Args:
            token: Runtime-only Bearer PAT.
            output: Evidence directory.

        Returns:
            None.
        """

        self._token = token
        self._output = output
        self._sequence = 0
        self.protocol_version: str | None = None
        self.session_id: str | None = None

    def call(self, label: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one MCP JSON-RPC request.

        Args:
            label: File-safe evidence label.
            method: JSON-RPC method.
            params: Optional method parameters.

        Returns:
            Sanitized transport metadata plus parsed envelope and business data.
        """

        self._sequence += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": label, "method": method}
        if params is not None:
            payload["params"] = params
        if method == "notifications/initialized":
            payload.pop("id", None)
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
            ),
        }
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        if self.session_id:
            headers["MCP-Session-Id"] = self.session_id
        request = urllib.request.Request(
            MCP_URL,
            data=json.dumps(payload, separators=(",", ":")).encode(),
            headers=headers,
            method="POST",
        )
        started = time.monotonic()
        raw = b""
        response_headers: dict[str, str] = {}
        status = 0
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                raw = response.read()
                status = response.status
                response_headers = {key.lower(): value for key, value in response.headers.items()}
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
            response_headers = {key.lower(): value for key, value in exc.headers.items()}
        parse_error: str | None = None
        envelope: dict[str, Any] | None = None
        try:
            envelope = parse_mcp(raw, response_headers.get("content-type", ""))
        except Exception as exc:  # Preserve malformed-response diagnostics.
            parse_error = f"{type(exc).__name__}: {exc}"
        if response_headers.get("mcp-session-id"):
            self.session_id = response_headers["mcp-session-id"]
        if method == "initialize" and isinstance(envelope, dict):
            result = envelope.get("result")
            if isinstance(result, dict) and result.get("protocolVersion"):
                self.protocol_version = str(result["protocolVersion"])
        data, error_code = business_data(envelope)
        write_json(self._output / f"{self._sequence:02d}-{label}.request.json", payload)
        write_json(
            self._output / f"{self._sequence:02d}-{label}.response.json",
            envelope if envelope is not None else {"http_status": status, "parse_error": parse_error},
        )
        return {
            "label": label,
            "http_status": status,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "content_type": response_headers.get("content-type"),
            "parse_error": parse_error,
            "error_code": error_code,
            "data": data,
        }


def load_db_snapshot() -> dict[str, Any]:
    """Load one explicitly read-only database snapshot and roll it back.

    Returns:
        Definitions, completed formula evidence, sibling definitions, route
        counts, and persisted metric counts for the five target factors.

    Raises:
        KeyError: If the test database configuration is incomplete.
        pymysql.MySQLError: If the read-only transaction or a query fails.
    """

    config = yaml.safe_load((ROOT / "config" / "test.yaml").read_text(encoding="utf-8"))
    database = config["database"]
    connection = pymysql.connect(
        host=database["host"],
        port=int(database["port"]),
        user=database["username"],
        password=database["password"],
        database=database["name"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=15,
        read_timeout=180,
    )
    placeholders = ",".join(["%s"] * len(TARGET_IDS))
    rolled_back = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION READ ONLY")
            cursor.execute(
                "SELECT DATABASE() AS database_name, @@hostname AS host_name, "
                "CURRENT_USER() AS authenticated_user"
            )
            identity = dict(cursor.fetchone())
            cursor.execute(
                f"""SELECT d.id AS detail_id,d.factor_id,d.name,d.description,d.calc_logic,d.params,
                           d.status,d.updated_at,s.sub_factor_name,s.window AS catalog_window,
                           s.factor_bar_interval,s.formula_summary
                    FROM factors_details AS d
                    LEFT JOIN sub_factors AS s ON s.id=d.factor_id AND d.is_sub_factor_id=1
                    WHERE d.is_sub_factor_id=1 AND d.factor_id IN ({placeholders})
                    ORDER BY d.factor_id,d.id""",
                TARGET_IDS,
            )
            details = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                """SELECT d.id AS detail_id,d.factor_id,d.name,d.description,d.calc_logic,d.params,
                          d.status,d.updated_at,s.window AS catalog_window,s.factor_bar_interval,
                          s.formula_summary
                   FROM factors_details AS d
                   LEFT JOIN sub_factors AS s ON s.id=d.factor_id AND d.is_sub_factor_id=1
                   WHERE d.is_sub_factor_id=1
                     AND (d.name LIKE %s OR d.name LIKE %s)
                   ORDER BY d.name,d.factor_id""",
                ("funding_acceleration__fr_diff2_%", "long_short_ratio__ls_chg_%"),
            )
            siblings = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                f"""SELECT e.id,e.run_id,e.factor_id,e.calculation_mode,e.factor_window_bars,
                           e.factor_bar_interval,e.return_bar_interval,e.forward_return_bars,
                           e.expression,e.formula_hash,e.formula_version,e.source_detail_id,
                           e.lookback_json,e.metadata_complete,e.recorded_at,r.status AS run_status,
                           r.completed_at
                    FROM factor_ic_run_formula_evidence AS e
                    INNER JOIN factor_ic_runs AS r ON r.run_id=e.run_id
                    WHERE e.is_sub_factor_id=1 AND e.factor_id IN ({placeholders})
                      AND r.status='completed'
                    ORDER BY e.factor_id,r.completed_at DESC,e.recorded_at DESC,e.id DESC""",
                TARGET_IDS,
            )
            evidence_rows = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                f"""SELECT factor_id,COUNT(*) AS route_count,
                           SUM(is_active=1) AS active_count,
                           SUM(is_active=1 AND is_eligible=1) AS active_eligible_count
                    FROM market_environment_factor_route
                    WHERE factor_id IN ({placeholders})
                    GROUP BY factor_id""",
                TARGET_IDS,
            )
            routes = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                f"""SELECT factor_id,run_id,COUNT(*) AS summary_count
                    FROM factor_ic_summary_metrics
                    WHERE is_sub_factor_id=1 AND factor_id IN ({placeholders})
                    GROUP BY factor_id,run_id""",
                TARGET_IDS,
            )
            summary_counts = [dict(row) for row in cursor.fetchall()]
    finally:
        connection.rollback()
        rolled_back = True
        connection.close()

    latest: dict[int, dict[str, Any]] = {}
    for row in evidence_rows:
        latest.setdefault(int(row["factor_id"]), row)
    selected_runs = {(factor_id, row["run_id"]) for factor_id, row in latest.items()}
    selected_summary_counts = [
        row for row in summary_counts if (int(row["factor_id"]), row["run_id"]) in selected_runs
    ]
    return {
        "transaction": {
            "begin_statement": "START TRANSACTION READ ONLY",
            "end_statement": "ROLLBACK",
            "rollback_completed": rolled_back,
        },
        "identity": {
            "database_name": identity["database_name"],
            "host_sha256": hashlib.sha256(str(identity["host_name"]).encode()).hexdigest(),
            "current_user_sha256": hashlib.sha256(
                str(identity["authenticated_user"]).encode()
            ).hexdigest(),
        },
        "details": details,
        "family_siblings": siblings,
        "latest_completed_evidence": latest,
        "route_counts": routes,
        "latest_run_summary_counts": selected_summary_counts,
    }


def formula_request(evidence: dict[str, Any]) -> dict[str, Any]:
    """Build an exact ``factor_get_formula`` identity request.

    Args:
        evidence: One immutable completed-run formula evidence row.

    Returns:
        MCP tool arguments for the exact run identity.
    """

    return {
        "factor_ref": f"sub_factor:{int(evidence['factor_id'])}",
        "run_id": evidence["run_id"],
        "calculation_mode": evidence["calculation_mode"],
        "interval": evidence["factor_bar_interval"],
        "factor_window_bars": evidence["factor_window_bars"],
        "return_bar_interval": evidence["return_bar_interval"],
        "forward_return_bars": int(evidence["forward_return_bars"]),
    }


def adjudicate(
    detail: dict[str, Any],
    evidence: dict[str, Any] | None,
    detail_call: dict[str, Any],
    formula_call: dict[str, Any] | None,
    route: dict[str, Any] | None,
    summary_count: dict[str, Any] | None,
) -> dict[str, Any]:
    """Adjudicate one factor from independent DB, MCP, and AST evidence.

    Args:
        detail: Current database definition.
        evidence: Latest completed immutable formula evidence, if any.
        detail_call: Current MCP definition response summary.
        formula_call: Exact-run MCP formula response summary, if available.
        route: Route usage counts.
        summary_count: Persisted metric count for the selected run.

    Returns:
        One structured PASS/FAIL/BLOCKED result.
    """

    factor_id = int(detail["factor_id"])
    params = decode_json(detail.get("params"))
    declared = numeric_window(
        params.get("factor_window_bars")
        or params.get("window")
        or detail.get("catalog_window")
    )
    current_expression = str(detail.get("calc_logic") or detail.get("formula_summary") or "")
    run_expression = str((evidence or {}).get("expression") or "")
    mcp_expression = str(((formula_call or {}).get("data") or {}).get("expression") or "")
    try:
        current_offsets = formula_offsets(current_expression)
        current_error = None
    except (SyntaxError, ValueError) as exc:
        current_offsets = []
        current_error = f"{type(exc).__name__}: {exc}"
    try:
        run_offsets = formula_offsets(run_expression) if run_expression else []
        run_error = None
    except (SyntaxError, ValueError) as exc:
        run_offsets = []
        run_error = f"{type(exc).__name__}: {exc}"
    current_span = max(current_offsets) if current_offsets else None
    run_span = max(run_offsets) if run_offsets else None
    mcp_matches_db = bool(
        evidence
        and formula_call
        and formula_call["http_status"] == 200
        and formula_call["error_code"] is None
        and mcp_expression == run_expression
        and formula_call["data"].get("formula_hash") == evidence.get("formula_hash")
        and formula_call["data"].get("run_id") == evidence.get("run_id")
    )
    detail_data = detail_call.get("data") or {}
    mcp_detail_matches_db = bool(
        detail_call["http_status"] == 200
        and detail_call["error_code"] is None
        and str(detail_data.get("formula_summary") or "") == current_expression
        and numeric_window(detail_data.get("window")) == declared
    )

    if declared is None or current_error or not evidence or run_error or not formula_call:
        status = "BLOCKED"
        failure_class = "BLOCKED_DATA_PRECONDITION"
        reason = "缺少可解析的声明窗口、当前公式或 completed-run 不可变公式证据。"
    elif not mcp_detail_matches_db or not mcp_matches_db:
        status = "FAIL"
        failure_class = "FAIL_DATA"
        reason = "MCP 当前定义或精确 Run 公式投影与测试库权威记录不一致。"
    elif current_span != declared or run_span != declared:
        status = "FAIL"
        failure_class = "FAIL_DATA"
        reason = (
            f"声明窗口为 {declared} 个 1H bar，但当前公式与最新 completed Run 的"
            f"实际原始数据依赖跨度均为 {current_span}/{run_span} 个 bar；窗口参数没有进入公式语义。"
        )
    else:
        status = "PASS"
        failure_class = None
        reason = (
            f"声明窗口为 {declared} 个 1H bar，公式所需 offsets={current_offsets}，"
            f"最大依赖跨度正好为 {current_span}；声明没有要求某个单独算子必须出现 {declared}。"
        )

    return {
        "case_id": f"CALC-510-sub_factor-{factor_id}",
        "factor_ref": f"sub_factor:{factor_id}",
        "sub_factor_id": factor_id,
        "detail_id": detail.get("detail_id"),
        "name": detail.get("name"),
        "description": detail.get("description"),
        "declared_window_bars": declared,
        "factor_bar_interval": detail.get("factor_bar_interval"),
        "current_definition": {
            "expression": current_expression,
            "dependency_offsets_bars": current_offsets,
            "dependency_span_bars": current_span,
            "parse_error": current_error,
            "updated_at": detail.get("updated_at"),
        },
        "latest_completed_run": {
            "run_id": (evidence or {}).get("run_id"),
            "completed_at": (evidence or {}).get("completed_at"),
            "expression": run_expression or None,
            "dependency_offsets_bars": run_offsets,
            "dependency_span_bars": run_span,
            "parse_error": run_error,
            "formula_hash": (evidence or {}).get("formula_hash"),
            "formula_evidence_id": (evidence or {}).get("id"),
            "persisted_summary_count": int(summary_count["summary_count"]) if summary_count else 0,
        },
        "mcp_projection": {
            "detail_http_status": detail_call["http_status"],
            "detail_error_code": detail_call["error_code"],
            "detail_matches_db": mcp_detail_matches_db,
            "formula_http_status": (formula_call or {}).get("http_status"),
            "formula_error_code": (formula_call or {}).get("error_code"),
            "formula_matches_db_evidence": mcp_matches_db,
            "reported_lookback": ((formula_call or {}).get("data") or {}).get("lookback"),
            "reported_metric_identity": ((formula_call or {}).get("data") or {}).get("metric_identity"),
        },
        "route_usage": route or {"route_count": 0, "active_count": 0, "active_eligible_count": 0},
        "expected_formula_shape_under_family_convention": EXPECTED_SHAPES[factor_id],
        "status": status,
        "failure_class": failure_class,
        "reason": reason,
    }


def main() -> None:
    """Run CALC-510 adjudication and write authoritative JSON/Markdown reports.

    Returns:
        None. The output directory is printed to stdout.

    Raises:
        SystemExit: If the runtime MCP token is absent.
        OSError: If network or artifact operations fail.
        pymysql.MySQLError: If database verification fails.
    """

    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise SystemExit(f"{TOKEN_ENV} is required")
    now = datetime.now(SHANGHAI)
    stamp = now.strftime("%Y%m%dT%H%M%S%z")
    output = ROOT / "reports" / "factor4-resume" / f"{stamp}-fixed-horizon-adjudication"
    output.mkdir(parents=True, exist_ok=False)

    snapshot = load_db_snapshot()
    write_json(output / "db-snapshot.json", snapshot)
    details = {int(row["factor_id"]): row for row in snapshot["details"]}
    evidence = {int(key): value for key, value in snapshot["latest_completed_evidence"].items()}
    routes = {int(row["factor_id"]): row for row in snapshot["route_counts"]}
    summaries = {
        (int(row["factor_id"]), row["run_id"]): row
        for row in snapshot["latest_run_summary_counts"]
    }

    client = McpClient(token, output)
    init = client.call(
        "INIT",
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "QuestTest-CALC-510-adjudication", "version": "1.0"},
        },
    )
    notify = client.call("NOTIFY", "notifications/initialized", {})
    checks: list[dict[str, Any]] = []
    call_summaries: list[dict[str, Any]] = [init, notify]
    for factor_id in TARGET_IDS:
        detail = details[factor_id]
        detail_call = client.call(
            f"DETAIL-{factor_id}",
            "tools/call",
            {
                "name": "factor_get_detail",
                "arguments": {"factor_ref": f"sub_factor:{factor_id}", "detail_level": "definition"},
            },
        )
        call_summaries.append(detail_call)
        row = evidence.get(factor_id)
        formula_call = None
        if row:
            formula_call = client.call(
                f"FORMULA-{factor_id}",
                "tools/call",
                {"name": "factor_get_formula", "arguments": formula_request(row)},
            )
            call_summaries.append(formula_call)
        checks.append(
            adjudicate(
                detail,
                row,
                detail_call,
                formula_call,
                routes.get(factor_id),
                summaries.get((factor_id, row["run_id"])) if row else None,
            )
        )

    counts = {status: sum(item["status"] == status for item in checks) for status in ("PASS", "FAIL", "BLOCKED")}
    overall_status = "FAIL" if counts["FAIL"] else ("BLOCKED" if counts["BLOCKED"] else "PASS")
    report = {
        "case_id": "CALC-510",
        "title": "因子公式窗口一致性：固定周期候选裁决",
        "captured_at": now.isoformat(),
        "environment": {
            "name": "test",
            "mcp_url": MCP_URL,
            "mode": "READ_ONLY",
            "database": snapshot["identity"],
            "database_transaction": snapshot["transaction"],
        },
        "scope": {
            "included_sub_factor_ids": list(TARGET_IDS),
            "excluded": ["VWAP", "DPO", "体验问题", "规范问题"],
        },
        "basis": {
            "declared_horizon": "factors_details.params.factor_window_bars / sub_factors.window",
            "actual_horizon": "独立解析公式 AST 后得到的最大原始输入 offset；不是把多个算子参数简单逐项对比",
            "persistence": "最新 completed factor_ic_run_formula_evidence 及同 Run 的 summary 行数",
            "mcp": "factor_get_detail 当前定义 + factor_get_formula 精确 completed-run 身份",
            "funding_second_difference_identity": "diff(h).diff(h) = x[t] - 2*x[t-h] + x[t-2h]，总依赖跨度为 2h",
        },
        "overall_status": overall_status,
        "counts": counts,
        "checks": checks,
        "family_siblings": snapshot["family_siblings"],
        "mcp_calls": [
            {
                "label": call["label"],
                "http_status": call["http_status"],
                "elapsed_seconds": call["elapsed_seconds"],
                "parse_error": call["parse_error"],
                "error_code": call["error_code"],
            }
            for call in call_summaries
        ],
        "reproduction": [
            "export FACTOR4_MCP_TOKEN='<test PAT loaded at runtime>'",
            "python tmp/fixed_horizon_adjudication.py",
            "Inspect results.json, db-snapshot.json, and DETAIL/FORMULA request-response artifacts in the printed directory.",
        ],
    }
    write_json(output / "results.json", report)

    lines = [
        "# CALC-510 固定周期公式裁决",
        "",
        f"- 执行时间：`{now.isoformat()}`",
        f"- 总结果：`{overall_status}`；`{counts['PASS']} PASS / {counts['FAIL']} FAIL / {counts['BLOCKED']} BLOCKED`",
        "- 数据库：`START TRANSACTION READ ONLY` 后查询，结束显式 `ROLLBACK`。",
        "- 排除范围：VWAP、DPO、体验与规范类问题。",
        "",
        "## 逐因子结果",
        "",
    ]
    for item in checks:
        lines.extend(
            [
                f"### {item['factor_ref']} - {item['status']}",
                "",
                f"- 名称：`{item['name']}`；detail ID：`{item['detail_id']}`。",
                f"- 声明：`{item['declared_window_bars']} x {item['factor_bar_interval']}`。",
                f"- 当前公式：`{item['current_definition']['expression']}`。",
                f"- 原始依赖 offsets：`{item['current_definition']['dependency_offsets_bars']}`；实际跨度：`{item['current_definition']['dependency_span_bars']}` bars。",
                f"- 最新 completed Run：`{item['latest_completed_run']['run_id']}`；持久化 summary：`{item['latest_completed_run']['persisted_summary_count']}` 条。",
                f"- MCP/DB：当前定义一致=`{item['mcp_projection']['detail_matches_db']}`；精确 Run 公式一致=`{item['mcp_projection']['formula_matches_db_evidence']}`。",
                f"- 裁决：{item['reason']}",
                f"- 家族语义下的公式形态示例：`{item['expected_formula_shape_under_family_convention']}`。",
                "",
            ]
        )
    lines.extend(
        [
            "## 复现",
            "",
            "```bash",
            "export FACTOR4_MCP_TOKEN='<test PAT loaded at runtime>'",
            "python tmp/fixed_horizon_adjudication.py",
            "```",
            "",
            "请求体、响应体和只读 DB 快照均位于本目录；文件不包含 Authorization header、完整 Token 或数据库密码。",
        ]
    )
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "status": overall_status, "counts": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
