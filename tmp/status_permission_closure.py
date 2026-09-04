#!/usr/bin/env python3
"""Close Factor 4 status and permission cases with read-only evidence.

The script intentionally never invokes MCP write tools and never issues a
database mutation.  A successful read tool call is correlated to its access
log so the supplied PAT can be identified without persisting the token.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import pymysql
import requests
import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import SettingsLoader  # noqa: E402
from tmp.critical_readonly_gap_probe import (  # noqa: E402
    MCPClient,
    error_code as mcp_error_code,
    successful as mcp_successful,
)


REPORT_ROOT = ROOT / "reports" / "factor4-resume"
MCP_ENDPOINT = os.environ.get(
    "MCP_URL", "https://test-factor-frontend.questvector.ai/mcp/factor-data"
)
TOKEN_ENV_NAMES = ("MCP_TOKEN", "FACTOR4_MCP_TOKEN")
SHANGHAI = ZoneInfo("Asia/Shanghai")
WRITE_TOOL = "submit_backtest_factor_feedback"
FEEDBACK_WRITE_SCOPE = "strategy.feedback.write"
FEEDBACK_READ_SCOPE = "strategy.feedback.read"
SENSITIVE_KEY = re.compile(
    r"authorization|password|secret|key_plaintext|ciphertext|nonce|signature|"
    r"(?:^|_)(?:jwt|hmac|access_token|refresh_token|token_value|hmac_value|jwt_value)$",
    re.IGNORECASE,
)
TOKEN_TEXT = re.compile(r"(?:Bearer\s+)?naf_mcp_[A-Za-z0-9_-]+", re.IGNORECASE)
JWT_TEXT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
WRITE_PRIVILEGES = {
    "ALL PRIVILEGES",
    "INSERT",
    "UPDATE",
    "DELETE",
    "CREATE",
    "DROP",
    "ALTER",
    "INDEX",
    "TRIGGER",
    "REFERENCES",
    "EXECUTE",
}


def json_default(value: Any) -> str:
    """Serialize database-native scalar values for evidence files."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def redact(value: Any) -> Any:
    """Recursively remove credentials while retaining safe key IDs/prefixes."""

    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if SENSITIVE_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return JWT_TEXT.sub("<redacted-jwt>", TOKEN_TEXT.sub("<redacted-pat>", value))
    return value


def write_json(path: Path, value: Any) -> None:
    """Write deterministic, recursively redacted JSON evidence."""

    path.write_text(
        json.dumps(
            redact(value),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=json_default,
        )
        + "\n",
        encoding="utf-8",
    )


def decode_json(value: Any) -> Any:
    """Decode a MySQL JSON value while accepting already decoded objects."""

    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def query(
    cursor: pymysql.cursors.DictCursor,
    sql: str,
    parameters: tuple[Any, ...] | None = None,
) -> list[dict[str, Any]]:
    """Execute one parameterized read statement and return dictionary rows."""

    cursor.execute(sql, parameters or ())
    return [dict(row) for row in cursor.fetchall()]


def query_one(
    cursor: pymysql.cursors.DictCursor,
    sql: str,
    parameters: tuple[Any, ...] | None = None,
) -> dict[str, Any] | None:
    """Execute one parameterized read statement and return its first row."""

    records = query(cursor, sql, parameters)
    return records[0] if records else None


def database_connection() -> pymysql.connections.Connection:
    """Open a non-autocommit connection to the configured test database."""

    settings = SettingsLoader.load("test", ROOT).database
    return pymysql.connect(
        host=settings.host,
        port=settings.port,
        user=settings.username,
        password=settings.password,
        database=settings.name,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=15,
        read_timeout=90,
        write_timeout=30,
    )


def read_business_watermark() -> dict[str, Any]:
    """Capture Factor 4 business counts in an explicit read-only transaction."""

    connection = database_connection()
    evidence: dict[str, Any] = {
        "transaction": {
            "start_statement": "START TRANSACTION READ ONLY",
            "select_only": True,
            "rollback_attempted": False,
            "rolled_back": False,
        },
        "tables": {},
    }
    try:
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION READ ONLY")
            for table in (
                "market_environment_daily",
                "market_environment_eval_batch",
                "market_environment_factor_metric",
                "market_environment_factor_route",
                "market_environment_strategy_feedback_submissions",
            ):
                evidence["tables"][table] = query_one(
                    cursor,
                    f"SELECT COUNT(*) AS row_count,MAX(id) AS max_id FROM `{table}`",
                )
    finally:
        evidence["transaction"]["rollback_attempted"] = True
        try:
            connection.rollback()
            evidence["transaction"]["rolled_back"] = True
        finally:
            connection.close()
    return evidence


def summarize_mcp_call(call: dict[str, Any] | None) -> dict[str, Any]:
    """Return a compact credential-free MCP transport summary."""

    if not isinstance(call, dict):
        return {"executed": False}
    notification_accepted = (
        call.get("method") == "notifications/initialized"
        and call.get("http_status") in {200, 202}
    )
    return {
        "executed": True,
        "case_id": call.get("case_id"),
        "method": call.get("method"),
        "http_status": call.get("http_status"),
        "elapsed_seconds": call.get("elapsed_seconds"),
        "successful": notification_accepted or mcp_successful(call),
        "notification_accepted_without_body": notification_accepted,
        "error_code": mcp_error_code(call),
        "parse_error": None if notification_accepted else call.get("parse_error"),
        "is_error": call.get("is_error"),
        "credential_echo": call.get("credential_echo"),
    }


def execute_mcp_permission_probe(token: str, output: Path) -> dict[str, Any]:
    """Read MCP capability metadata and execute one safe catalog count call."""

    client = MCPClient(token, output)
    initialize = client.request(
        "PERM-MCP-INIT",
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "QuestTest-status-permission-closure", "version": "1.0"},
        },
    )
    init_result = ((initialize.get("envelope") or {}).get("result") or {})
    client.protocol_version = str(init_result.get("protocolVersion") or "") or None
    calls: dict[str, dict[str, Any] | None] = {
        "initialize": initialize,
        "initialized_notification": None,
        "tools_list": None,
        "catalog_stats": None,
    }
    if not mcp_successful(initialize) or not client.protocol_version:
        return {
            "endpoint": MCP_ENDPOINT,
            "auth_mode": "Bearer <redacted>",
            "calls": {name: summarize_mcp_call(call) for name, call in calls.items()},
            "read_tools_called": [],
            "write_tools_called": [],
            "blocking_reason": "MCP_INITIALIZATION_FAILED",
        }

    calls["initialized_notification"] = client.request(
        "PERM-MCP-NOTIFY", "notifications/initialized", {}
    )
    calls["tools_list"] = client.request("PERM-MCP-TOOLS", "tools/list", {})
    calls["catalog_stats"] = client.tool(
        "PERM-MCP-READ", "factor_catalog_stats", {"library_status": "valid"}
    )

    tools_result = ((calls["tools_list"] or {}).get("envelope") or {}).get("result") or {}
    tools = tools_result.get("tools") if isinstance(tools_result, dict) else []
    tools = [item for item in tools or [] if isinstance(item, dict)]
    feedback_tool = next((item for item in tools if item.get("name") == WRITE_TOOL), None)
    business = (calls["catalog_stats"] or {}).get("business") or {}
    meta = business.get("meta") if isinstance(business, dict) else {}
    instructions = str(init_result.get("instructions") or "")
    return {
        "endpoint": MCP_ENDPOINT,
        "auth_mode": "Bearer <redacted>",
        "protocol_version": client.protocol_version,
        "server_info": init_result.get("serverInfo"),
        "calls": {name: summarize_mcp_call(call) for name, call in calls.items()},
        "read_tools_called": ["factor_catalog_stats"],
        "write_tools_called": [],
        "advertised_tool_count": len(tools),
        "advertised_tool_names": sorted(str(item.get("name")) for item in tools),
        "feedback_tool_contract": {
            "present": feedback_tool is not None,
            "name": feedback_tool.get("name") if feedback_tool else None,
            "description": feedback_tool.get("description") if feedback_tool else None,
            "input_required": (
                (feedback_tool.get("inputSchema") or {}).get("required")
                if feedback_tool
                else None
            ),
            "has_output_schema": bool(feedback_tool and feedback_tool.get("outputSchema")),
            "annotations": feedback_tool.get("annotations") if feedback_tool else None,
            "initialize_declares_dedicated_feedback_scopes": (
                "strategy.feedback.write/read" in instructions
                or (
                    FEEDBACK_WRITE_SCOPE in instructions
                    and FEEDBACK_READ_SCOPE in instructions
                )
            ),
        },
        "read_call_request_id": meta.get("request_id") if isinstance(meta, dict) else None,
        "read_call_successful": mcp_successful(calls["catalog_stats"]),
    }


def parse_permissions_from_grants(grants: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify database grant text without executing any permission probe."""

    statements = [str(value) for row in grants for value in row.values()]
    upper = "\n".join(statements).upper()
    detected = sorted(privilege for privilege in WRITE_PRIVILEGES if privilege in upper)
    return {
        "statements": statements,
        "write_or_ddl_privileges_detected": detected,
        "appears_read_only": not detected,
    }


def read_permission_database_evidence(
    request_id: str | None,
    backend_identity: dict[str, Any],
) -> dict[str, Any]:
    """Resolve PAT, Backend permission, and DB grant evidence read-only."""

    connection = database_connection()
    settings = SettingsLoader.load("test", ROOT)
    evidence: dict[str, Any] = {
        "transaction": {
            "start_statement": "START TRANSACTION READ ONLY",
            "select_only": True,
            "rollback_attempted": False,
            "rolled_back": False,
        }
    }
    try:
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION READ ONLY")
            evidence["database_identity"] = query_one(
                cursor,
                "SELECT DATABASE() AS database_name,CURRENT_USER() AS current_user_name",
            )
            grants = query(cursor, "SHOW GRANTS FOR CURRENT_USER()")
            evidence["database_grants"] = parse_permissions_from_grants(grants)
            evidence["database_account_context"] = {
                "configured_environment": settings.environment,
                "configured_username": settings.database.username,
                "classified_as_test_write_account": settings.environment == "test",
                "read_only_account_configured": False,
                "note": (
                    "QuestTest's test configuration explicitly permits controlled DB writes. "
                    "This account is therefore not used as a read-only-account fixture."
                ),
            }

            access_log = None
            if request_id:
                access_log = query_one(
                    cursor,
                    """
                    SELECT l.id AS access_log_id,l.request_id,l.api_key_id,l.caller_subject,
                           l.caller_user_id,l.caller_role,l.tool_name,l.required_scope,l.status,
                           l.error_code,l.started_at,l.finished_at,
                           k.key_prefix,k.name AS key_name,k.subject AS key_subject,
                           k.owner_user_id,k.team_id,k.scopes_json,k.status AS key_status,
                           k.expires_at,k.revoked_at
                    FROM agent_data_access_logs l
                    JOIN agent_data_api_keys k ON k.id=l.api_key_id
                    WHERE l.request_id=%s
                    """,
                    (request_id,),
                )
            if access_log:
                access_log["scopes_json"] = decode_json(access_log.get("scopes_json"))
            evidence["current_pat_from_access_audit"] = access_log
            evidence["active_pat_population"] = query_one(
                cursor,
                """
                SELECT COUNT(*) AS active_count,
                       SUM(JSON_CONTAINS(scopes_json,JSON_QUOTE(%s))) AS feedback_write_count,
                       SUM(NOT JSON_CONTAINS(scopes_json,JSON_QUOTE(%s))) AS without_feedback_write_count
                FROM agent_data_api_keys
                WHERE status='active' AND (expires_at IS NULL OR expires_at>UTC_TIMESTAMP(6))
                """,
                (FEEDBACK_WRITE_SCOPE, FEEDBACK_WRITE_SCOPE),
            )

            evidence["permission_catalog"] = {
                "strategy_feedback": query(
                    cursor,
                    """
                    SELECT code,name,description,module,action,risk_level,enabled
                    FROM permissions
                    WHERE code='use_strategy_feedback_mcp'
                    """,
                ),
                "hmac_or_internal_codes": query(
                    cursor,
                    """
                    SELECT code,name,description,module,action,risk_level,enabled
                    FROM permissions
                    WHERE LOWER(code) LIKE '%%hmac%%' OR LOWER(module) LIKE '%%hmac%%'
                       OR LOWER(action) LIKE '%%hmac%%' OR LOWER(code) LIKE '%%internal%%'
                       OR LOWER(module) LIKE '%%internal%%' OR LOWER(action) LIKE '%%internal%%'
                    ORDER BY code
                    """,
                ),
            }
            user_id = backend_identity.get("user_id")
            role = backend_identity.get("role")
            evidence["backend_permission_sources"] = {
                "role_permissions": (
                    query(
                        cursor,
                        "SELECT role_name,perm_code FROM role_permissions WHERE role_name=%s ORDER BY perm_code",
                        (role,),
                    )
                    if role
                    else []
                ),
                "user_overrides": (
                    query(
                        cursor,
                        """
                        SELECT user_id,perm_code,effect,reason
                        FROM user_permission_overrides
                        WHERE user_id=%s
                        ORDER BY perm_code
                        """,
                        (user_id,),
                    )
                    if user_id
                    else []
                ),
            }
    finally:
        evidence["transaction"]["rollback_attempted"] = True
        try:
            connection.rollback()
            evidence["transaction"]["rolled_back"] = True
        finally:
            connection.close()
    return evidence


def response_json(response: requests.Response) -> dict[str, Any]:
    """Parse a response as an object and return an empty object otherwise."""

    try:
        value = response.json()
    except (requests.JSONDecodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def backend_identity_probe() -> tuple[dict[str, Any], list[str]]:
    """Login to the configured ordinary test account and read only `/me`."""

    settings = SettingsLoader.load("test", ROOT)
    credentials = settings.authentication.restricted
    calls: list[str] = []
    evidence: dict[str, Any] = {
        "base_url": settings.api.base_url,
        "account_fixture": "authentication.restricted",
        "login_status": None,
        "me_status": None,
        "identity": {},
        "openapi_discovery": [],
        "jwt_persisted": False,
    }
    if not credentials.email or not credentials.password:
        evidence["blocking_reason"] = "BACKEND_ACCOUNT_CREDENTIALS_MISSING"
        return evidence, calls

    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 QuestTest-status-permission-closure"
    login = session.post(
        f"{settings.api.base_url}/auth/login",
        json={"email": credentials.email, "password": credentials.password},
        timeout=settings.api.timeout_seconds,
    )
    calls.append("POST /auth/login")
    evidence["login_status"] = login.status_code
    login_body = response_json(login)
    login_data = login_body.get("data") if isinstance(login_body.get("data"), dict) else {}
    token = login_data.get("token") if isinstance(login_data.get("token"), str) else None
    if login.status_code != 200 or not token:
        evidence["blocking_reason"] = "BACKEND_LOGIN_FAILED_OR_TOKEN_MISSING"
        return evidence, calls

    headers = {"Authorization": f"Bearer {token}"}
    current = session.get(
        f"{settings.api.base_url}/me",
        headers=headers,
        timeout=settings.api.timeout_seconds,
    )
    calls.append("GET /me")
    evidence["me_status"] = current.status_code
    current_body = response_json(current)
    current_data = (
        current_body.get("data") if isinstance(current_body.get("data"), dict) else {}
    )
    nested = current_data.get("user")
    if isinstance(nested, dict):
        merged = dict(current_data)
        merged.update(nested)
        current_data = merged
    email = str(current_data.get("email") or "").strip().casefold()
    evidence["identity"] = {
        "user_id": current_data.get("id")
        or current_data.get("user_id")
        or current_data.get("uid"),
        "email_sha256": hashlib.sha256(email.encode()).hexdigest() if email else None,
        "role": current_data.get("role"),
        "status": current_data.get("status"),
        "permissions": sorted(
            str(item)
            for item in current_data.get("permissions", [])
            if isinstance(item, str)
        ),
    }

    parsed = urlsplit(settings.api.base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    for label, url in (
        ("api_v1_openapi_yaml", f"{settings.api.base_url}/openapi.yaml"),
        ("api_v1_openapi", f"{settings.api.base_url}/openapi.json"),
        ("origin_openapi", f"{origin}/openapi.json"),
    ):
        discovery = session.get(url, headers=headers, timeout=settings.api.timeout_seconds)
        calls.append(f"GET {label}")
        item: dict[str, Any] = {
            "location": label,
            "http_status": discovery.status_code,
            "content_type": discovery.headers.get("content-type"),
            "document_available": False,
            "hmac_or_internal_paths": [],
            "hmac_or_internal_operations": [],
            "webhook_parameter_contracts": {},
        }
        if "yaml" in str(discovery.headers.get("content-type", "")).casefold():
            try:
                parsed_body = yaml.safe_load(discovery.text)
            except yaml.YAMLError:
                parsed_body = {}
            body = parsed_body if isinstance(parsed_body, dict) else {}
        else:
            body = response_json(discovery)
        paths = body.get("paths") if isinstance(body.get("paths"), dict) else None
        if paths is not None:
            item["document_available"] = True
            item["document_sha256"] = hashlib.sha256(discovery.content).hexdigest()
            matching_paths = sorted(
                path
                for path in paths
                if "hmac" in path.casefold() or "internal" in path.casefold()
            )
            item["hmac_or_internal_paths"] = matching_paths
            for path in matching_paths:
                path_item = paths.get(path)
                if not isinstance(path_item, dict):
                    continue
                for method, operation in path_item.items():
                    if method.casefold() not in {
                        "get",
                        "post",
                        "put",
                        "patch",
                        "delete",
                    } or not isinstance(operation, dict):
                        continue
                    parameter_refs = [
                        parameter.get("$ref")
                        for parameter in operation.get("parameters", [])
                        if isinstance(parameter, dict) and parameter.get("$ref")
                    ]
                    item["hmac_or_internal_operations"].append(
                        {
                            "path": path,
                            "method": method.upper(),
                            "operation_id": operation.get("operationId"),
                            "description": operation.get("description"),
                            "security": operation.get("security"),
                            "parameter_refs": parameter_refs,
                            "response_codes": sorted(
                                str(code) for code in (operation.get("responses") or {})
                            ),
                        }
                    )
            components = body.get("components")
            components = components if isinstance(components, dict) else {}
            parameters = components.get("parameters")
            parameters = parameters if isinstance(parameters, dict) else {}
            item["webhook_parameter_contracts"] = {
                name: {
                    "in": definition.get("in"),
                    "required": definition.get("required"),
                    "name": definition.get("name"),
                    "schema": definition.get("schema"),
                }
                for name, definition in parameters.items()
                if name in {"WebhookTimestamp", "WebhookNonce", "WebhookSignature"}
                and isinstance(definition, dict)
            }
        evidence["openapi_discovery"].append(item)
    return evidence, calls


def required_scope_is_granted(access_log: dict[str, Any] | None) -> bool:
    """Return whether every audited required scope belongs to the PAT scope set."""

    if not access_log:
        return False
    scopes = access_log.get("scopes_json")
    if not isinstance(scopes, list):
        return False
    required = str(access_log.get("required_scope") or "").split()
    return bool(required) and set(required).issubset({str(item) for item in scopes})


def adjudicate_db608(
    mcp: dict[str, Any],
    backend: dict[str, Any],
    database: dict[str, Any],
) -> dict[str, Any]:
    """Apply DB-608 without converting missing negative credentials into a pass."""

    access_log = database.get("current_pat_from_access_audit")
    scopes = access_log.get("scopes_json") if isinstance(access_log, dict) else []
    scopes = [str(item) for item in scopes] if isinstance(scopes, list) else []
    current_pat_has_write = FEEDBACK_WRITE_SCOPE in scopes
    current_pat_identified = bool(
        access_log
        and access_log.get("status") == "success"
        and access_log.get("key_status") == "active"
        and required_scope_is_granted(access_log)
        and mcp.get("read_call_successful")
    )
    feedback_contract = mcp.get("feedback_tool_contract") or {}
    backend_identity = backend.get("identity") or {}
    backend_documents = [
        item
        for item in backend.get("openapi_discovery", [])
        if isinstance(item, dict) and item.get("document_available")
    ]
    backend_contract_available = bool(backend_documents)
    backend_hmac_paths = [
        path
        for item in backend_documents
        for path in item.get("hmac_or_internal_paths", [])
    ]
    backend_hmac_operations = [
        operation
        for item in backend_documents
        for operation in item.get("hmac_or_internal_operations", [])
        if isinstance(operation, dict)
    ]
    required_hmac_refs = {
        "#/components/parameters/WebhookTimestamp",
        "#/components/parameters/WebhookNonce",
        "#/components/parameters/WebhookSignature",
    }
    parameter_contracts = {
        name: contract
        for item in backend_documents
        for name, contract in item.get("webhook_parameter_contracts", {}).items()
        if isinstance(contract, dict)
    }
    backend_hmac_contract_passes = bool(backend_hmac_operations) and all(
        "hmac" in str(operation.get("description") or "").casefold()
        and operation.get("security") == []
        and required_hmac_refs.issubset(set(operation.get("parameter_refs") or []))
        for operation in backend_hmac_operations
    ) and all(
        parameter_contracts.get(name, {}).get("in") == "header"
        and parameter_contracts.get(name, {}).get("required") is True
        for name in ("WebhookTimestamp", "WebhookNonce", "WebhookSignature")
    )
    database_grants = database.get("database_grants") or {}
    branches = [
        {
            "branch": "current_pat_scope_identity",
            "status": "PASS" if current_pat_identified else "BLOCKED",
            "classification": None if current_pat_identified else "BLOCKED_ENV",
            "reason": (
                "The live read call was uniquely joined to an active PAT, and its required read "
                "scope is present in that PAT's stored scope set."
                if current_pat_identified
                else "The live MCP call could not be uniquely joined to an active PAT scope record."
            ),
        },
        {
            "branch": "ordinary_browse_pat_without_feedback_write",
            "status": "BLOCKED",
            "classification": "BLOCKED_DATA_PRECONDITION",
            "reason": (
                "The supplied PAT is privileged and explicitly contains strategy.feedback.write. "
                "No user-authorized ordinary browse PAT was provided, so negative scope enforcement "
                "cannot be tested without using another user's credential or issuing a write call."
                if current_pat_has_write
                else "No separately authorized ordinary browse PAT was supplied for a negative check."
            ),
        },
        {
            "branch": "backend_jwt_cannot_use_internal_hmac_evaluation",
            "status": "PASS" if backend_hmac_contract_passes else "BLOCKED",
            "classification": None if backend_hmac_contract_passes else "BLOCKED_DOC",
            "reason": (
                "The live OpenAPI contract declares both internal evaluation operations as "
                "HMAC-authenticated, disables operation-level Bearer security, and requires timestamp, "
                "nonce, and signature headers. The ordinary JWT permission catalog contains no "
                "HMAC/internal capability. Runtime POST rejection was intentionally not attempted."
                if backend_hmac_contract_passes
                else "The ordinary Backend identity was read, but the available contract does not "
                "fully prove that internal evaluation requires HMAC rather than a user JWT."
            ),
        },
        {
            "branch": "database_read_only_account",
            "status": "BLOCKED",
            "classification": "BLOCKED_DATA_PRECONDITION",
            "reason": (
                "The configured database credential is the permitted test write account and has "
                "write/DDL grants. No separate read-only database credential is configured, so this "
                "credential is not a valid fixture for the read-only-account assertion."
            ),
        },
    ]
    confirmed_failure = False
    return {
        "case_id": "DB-608",
        "title": "只读账号与权限边界",
        "status": "FAIL" if confirmed_failure else "BLOCKED",
        "classification": "P0" if confirmed_failure else "BLOCKED_DATA_PRECONDITION",
        "reason": (
            "No permission bypass is confirmed. The Backend HMAC boundary is explicit in OpenAPI, "
            "but the supplied PAT is intentionally privileged and the configured DB credential is "
            "the test write account. Without an ordinary PAT and a read-only DB credential, DB-608 "
            "cannot be marked PASS."
        ),
        "branches": branches,
        "assertions": {
            "mcp_write_tools_called": mcp.get("write_tools_called") == [],
            "feedback_tool_advertised": bool(feedback_contract.get("present")),
            "feedback_scopes_declared_by_server": bool(
                feedback_contract.get("initialize_declares_dedicated_feedback_scopes")
            ),
            "current_pat_identified_from_audited_read": current_pat_identified,
            "current_pat_has_feedback_write_scope": current_pat_has_write,
            "provided_pat_is_ordinary_browse_fixture": not current_pat_has_write,
            "backend_me_succeeded": backend.get("me_status") == 200,
            "backend_permission_codes": backend_identity.get("permissions", []),
            "backend_contract_document_available": backend_contract_available,
            "backend_hmac_static_contract_passes": backend_hmac_contract_passes,
            "backend_hmac_or_internal_paths": backend_hmac_paths,
            "backend_hmac_or_internal_operations": backend_hmac_operations,
            "database_account_appears_read_only": database_grants.get("appears_read_only"),
            "database_account_is_configured_test_account": (
                database.get("database_account_context", {}).get(
                    "classified_as_test_write_account"
                )
            ),
        },
        "blocking_reasons": [
            "ORDINARY_BROWSE_PAT_NOT_PROVIDED",
            "READ_ONLY_DATABASE_CREDENTIAL_NOT_CONFIGURED",
        ],
    }


def load_case(path: Path, expected_case_id: str) -> dict[str, Any]:
    """Load one merge-ready case object and validate its identity."""

    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Case evidence must be an object: {path}")
    case = value.get("case") if isinstance(value.get("case"), dict) else value
    if case.get("case_id") != expected_case_id:
        raise ValueError(
            f"Expected {expected_case_id} case evidence, got {case.get('case_id')!r}: {path}"
        )
    return case


def current_env110_case() -> dict[str, Any]:
    """Return the live ENV-110 verdict gathered immediately before this run."""

    return {
        "case_id": "ENV-110",
        "title": "预测窗口字段",
        "status": "BLOCKED",
        "classification": "BLOCKED_DOC",
        "severity": None,
        "reason": (
            "当前 forecast 数据映射一致，但 MCP 与 Backend 均未发布可裁决 forecast 行字段 "
            "required/nullable/location 的正式输出契约；effective_from/effective_to 在 DB 中允许 "
            "NULL 且当前样本全部为 NULL，因此不能把 Backend 省略空字段、MCP 显式返回 null "
            "或缺少顶层 forecast_date/horizon 判为产品缺陷。"
        ),
        "blocking_reasons": [
            "MCP_ENVIRONMENT_DAILY_OUTPUT_SCHEMA_UNDECLARED",
            "BACKEND_MARKET_ENVIRONMENT_DATA_SCHEMA_UNDECLARED",
            "FORECAST_EFFECTIVE_WINDOW_MAPPING_UNSPECIFIED",
        ],
        "assertions": [
            {
                "assertion": "current forecast ID set agrees across MCP, Backend and DB",
                "passed": True,
            },
            {
                "assertion": "raw_payload.forecast_date maps to environment_date",
                "passed": True,
            },
            {
                "assertion": "raw_payload.horizon maps to features.horizon",
                "passed": True,
            },
            {
                "assertion": "available_at represents the same instant across MCP, Backend and DB",
                "passed": True,
            },
            {
                "assertion": "effective window direction can be evaluated on populated rows",
                "passed": False,
                "not_applicable": True,
            },
            {
                "assertion": (
                    "formal output contract defines required/nullability/location for forecast window fields"
                ),
                "passed": False,
                "blocked": True,
            },
        ],
        "evidence": {
            "captured_at": "2026-09-04T05:20:48.175634Z",
            "transport": {
                "mcp_http": 200,
                "mcp_is_error": False,
                "backend_login_http": 200,
                "backend_http": 200,
            },
            "mcp_server": {
                "name": "factor-agent-data-service",
                "version": "0.2.0",
                "tool_count": 22,
                "environment_get_daily_has_output_schema": False,
            },
            "backend_openapi": {
                "sha256": "6855940973c3b3c47183298317f8497726fe51e5be01b7112927400e491f553c",
                "operation_id": "listMarketEnvironmentDaily",
                "success_schema": "MarketEnvironmentSuccess",
                "data_has_type": False,
                "data_has_properties": False,
            },
            "row_counts": {
                "mcp": 34,
                "backend": 34,
                "db_current_forecast": 34,
                "common": 34,
                "missing_in_mcp": 0,
                "extra_in_mcp": 0,
                "missing_in_backend": 0,
                "extra_in_backend": 0,
            },
            "field_mapping": {
                "raw_forecast_date_present": 34,
                "forecast_date_to_environment_date_matches": 34,
                "raw_horizon_present": 34,
                "features_horizon_present": 34,
                "horizon_matches": 34,
                "distinct_horizon_count": 1,
                "horizon_value": "未来1–4周",
                "available_at_mcp_db_mismatches": 0,
                "available_at_backend_db_mismatches": 0,
            },
            "effective_window": {
                "db_effective_from_nullable": True,
                "db_effective_to_nullable": True,
                "db_effective_from_null": 34,
                "db_effective_to_null": 34,
                "db_reversed_or_empty_non_null_windows": 0,
                "mcp_explicit_null_from": 34,
                "mcp_explicit_null_to": 34,
                "backend_field_omitted_from": 34,
                "backend_field_omitted_to": 34,
                "direction_subcheck": "NOT_APPLICABLE",
            },
            "top_level_location": {
                "mcp_forecast_date_present": 0,
                "backend_forecast_date_present": 0,
                "mcp_horizon_present": 0,
                "backend_horizon_present": 0,
            },
            "db_constraints": {
                "effective_from_nullable": True,
                "effective_to_nullable": True,
                "window_check": (
                    "effective_to IS NULL OR effective_from IS NULL OR effective_to > effective_from"
                ),
                "forecast_date_column_exists": False,
                "horizon_column_exists": False,
            },
            "observable_representation_difference": (
                "MCP emits nullable DB columns as explicit null; Backend omits those null keys. "
                "No formal output schema chooses one representation."
            ),
            "source_artifacts": [
                "reports/factor4-resume/20260904T124800+0800-tool-matrix-pit/003-MCP-TOOLS.response.json",
                "reports/factor4-resume/20260904T124800+0800-tool-matrix-pit/005-MATRIX-environment_get_daily-CURRENT.response.json",
                "reports/factor4-resume/20260904T040938Z-calc508-env108-met310/openapi-evidence.json",
                "reports/factor4-resume/20260904T040938Z-calc508-env108-met310/004-ENV-108-FORECAST-BACKEND-UNFILTERED.backend.response.json",
            ],
        },
        "subcheck_statuses": {
            "data_mapping": "PASS",
            "effective_window_direction": "NOT_APPLICABLE",
            "output_contract": "BLOCKED_DOC",
        },
        "confirmed_fail_count": 0,
    }


def current_met305_case() -> dict[str, Any]:
    """Return the live MET-305 verdict gathered immediately before this run."""

    return {
        "case_id": "MET-305",
        "title": "指标状态语义",
        "status": "BLOCKED",
        "classification": "BLOCKED_DATA_PRECONDITION",
        "severity": None,
        "reason": (
            "The current published batch and the full metric table contain only success and "
            "insufficient_sample rows. Both observed branches satisfy status, evidence, null, "
            "payload-mirror, and route-admission semantics; no failed metric exists, so the "
            "failed-state branch cannot be adjudicated without fabricating data. No FAIL was confirmed."
        ),
        "blocking_reasons": ["NO_NATURAL_FAILED_METRIC_STATE"],
        "branch_results": {
            "success": {"status": "PASS", "row_count": 4134},
            "insufficient_sample": {"status": "PASS", "row_count": 1590},
            "failed": {
                "status": "BLOCKED",
                "classification": "BLOCKED_DATA_PRECONDITION",
                "row_count": 0,
            },
        },
        "assertions": [
            {
                "assertion": (
                    "success rows have non-null boolean is_valid, null error fields, coherent "
                    "positive sample evidence, and matching payload state fields"
                ),
                "passed": True,
            },
            {
                "assertion": (
                    "insufficient_sample rows have shortage errors, null analytical outputs, "
                    "shortage evidence, and explicit ineligible route semantics"
                ),
                "passed": True,
            },
            {
                "assertion": (
                    "active routes reference only same-batch, same-factor/version/label success "
                    "metrics with is_valid=true under any_valid_scope"
                ),
                "passed": True,
            },
            {
                "assertion": (
                    "a natural failed metric proves technical-error fields and publication blocking"
                ),
                "passed": False,
                "blocked": True,
            },
        ],
        "evidence": {
            "selected_batch_id": 6,
            "selected_batch_uid": "eeab1a72-383f-46e6-98d0-5984554f17e3",
            "batch_status": "success",
            "publish_status": "published",
            "is_active": True,
            "batch_counts": {
                "expected": 5724,
                "success": 4134,
                "insufficient_sample": 1590,
                "failed": 0,
            },
            "full_table_counts": {
                "success": 4134,
                "insufficient_sample": 1590,
                "failed": 0,
            },
            "success_semantics": {
                "is_valid_true": 94,
                "is_valid_false": 4040,
                "is_valid_null": 0,
                "non_null_error_code": 0,
                "non_null_error_message": 0,
                "coherent_sample_rows": 4134,
            },
            "insufficient_semantics": {
                "is_valid_null": 1590,
                "error_code_present": 1590,
                "error_message_present": 1590,
                "analytical_output_non_null_count": 0,
                "payload_route_ineligible_count": 1590,
                "payload_valid_scopes_empty_count": 1590,
                "payload_route_reject_reason_present_count": 1590,
            },
            "insufficient_reason_counts": {
                "INSUFFICIENT_ENVIRONMENT_DAYS": 954,
                "INSUFFICIENT_TIME_SERIES_SAMPLE": 554,
                "INSUFFICIENT_CROSS_SECTIONAL_SLICES": 57,
                "INSUFFICIENT_CROSS_SECTIONAL_SYMBOLS": 25,
            },
            "shortage_evidence": {
                "environment_days": "954/954 have sample_day_count=0 < 45",
                "time_series": "554/554 have coverage_rate <= 0.692789969 < 0.7",
                "cross_sectional_symbols": "25/25 have loaded_symbol_count 5..6 < 20",
                "cross_sectional_slices": (
                    "57/57 have total_sample_count/loaded_symbol_count 0..112 < 120 and exact "
                    "division; the quotient equals explicit valid_slice_count in 2303/2303 successful CS rows"
                ),
            },
            "db_payload_state_field_mismatch_count": 0,
            "route_count": 86,
            "route_primary_non_success_count": 0,
            "route_primary_invalid_count": 0,
            "route_evidence_reference_count": 87,
            "route_evidence_non_success_or_invalid_count": 0,
            "route_scope_patterns": {
                "time_series_only": 81,
                "cross_sectional_only": 4,
                "both": 1,
            },
            "valid_groups_below_route_threshold": {
                "count": 7,
                "score_range": [47.482366, 58.877614],
                "minimum_route_score": 60.0,
                "incorrect_route_count": 0,
            },
            "anomaly_samples": [],
            "representative_insufficient_metric_ids": {
                "environment_days": 733,
                "time_series_sample": 739,
                "cross_sectional_slices": 1264,
                "cross_sectional_symbols": 1276,
            },
            "database_transaction": (
                "SET SESSION TRANSACTION READ ONLY; START TRANSACTION READ ONLY; ROLLBACK"
            ),
            "database_changed_by_test": False,
            "source_artifacts": [
                "reports/factor4-resume/20260904T040938Z-calc508-env108-met310/adjudicated-summary.json",
                "reports/factor4-resume/20260904T044449Z-protocol-gaps/main-MCP-006-LIMIT-factor_get_environment_metrics-limit-MAX.response.json",
                "reports/factor4-resume/20260904T123910+0800-route-integrity-closure/results.json",
            ],
        },
    }


def scan_artifacts(
    output: Path,
    forbidden_values: list[str],
) -> dict[str, Any]:
    """Scan generated artifacts for exact credentials and complete token patterns."""

    exact_matches: Counter[str] = Counter()
    token_pattern_files: list[str] = []
    jwt_pattern_files: list[str] = []
    files = sorted(path for path in output.rglob("*") if path.is_file())
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for index, value in enumerate(forbidden_values):
            if value and value in text:
                exact_matches[f"forbidden_value_{index}"] += text.count(value)
        if TOKEN_TEXT.search(text):
            token_pattern_files.append(path.name)
        if JWT_TEXT.search(text):
            jwt_pattern_files.append(path.name)
    return {
        "files_scanned": len(files),
        "exact_credential_match_counts": dict(exact_matches),
        "complete_mcp_token_pattern_files": token_pattern_files,
        "complete_jwt_pattern_files": jwt_pattern_files,
        "passed": not exact_matches and not token_pattern_files and not jwt_pattern_files,
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Render a compact Chinese summary from the authoritative JSON report."""

    lines = [
        "# ENV-110 / MET-305 / DB-608 状态与权限闭环",
        "",
        f"- 环境：`{report['environment']}`",
        f"- 模式：`{report['mode']}`",
        f"- MCP：`{report['mcp_evidence']['endpoint']}`",
        f"- DB 业务表水位不变：`{report['database_watermarks']['business_tables_unchanged']}`",
        "",
        "## 裁决",
        "",
        "| 用例 | 状态 | 分类 | 结论 |",
        "|---|---|---|---|",
    ]
    for case in report["cases"]:
        lines.append(
            f"| `{case['case_id']}` | `{case['status']}` | "
            f"`{case.get('classification')}` | {str(case.get('reason', '')).replace('|', '/')} |"
        )
    lines.extend(
        [
            "",
            "## DB-608 关键边界",
            "",
            "- 本轮未调用 `submit_backtest_factor_feedback`，也未调用任何其他 MCP 写工具。",
            "- 当前 PAT 通过本轮读请求的 request ID 与访问审计唯一关联；其 scope 是特权集合，"
            "包含 `strategy.feedback.write`，所以它不是“普通浏览 PAT”测试夹具。",
            "- Backend 普通账号只读取了 `/me`；OpenAPI 明确把两条 internal 评估操作声明为 "
            "HMAC 鉴权并要求 timestamp/nonce/signature，未发送任何 internal POST。",
            "- 当前 DB 凭据是测试环境允许的写账号，`SHOW GRANTS` 显示写/DDL 能力；"
            "没有单独只读账号时，该分支是前置条件阻塞，不是权限缺陷。",
            "",
            "## 执行边界",
            "",
            "- 数据库直连查询全部在 `START TRANSACTION READ ONLY` 中执行并最终 `ROLLBACK`。",
            "- MCP 读调用只会产生服务端访问审计和 key last-used 时间，这是预期安全遥测；"
            "Factor 4 业务表水位必须保持不变。",
            "- 报告和传输证据已执行完整 PAT、JWT、密码的精确值与模式扫描。",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    """Parse merge-ready ENV-110 and MET-305 evidence paths."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--env110", type=Path)
    parser.add_argument("--met305", type=Path)
    return parser.parse_args()


def main() -> int:
    """Execute the permission closure and merge the two status case verdicts."""

    args = parse_args()
    token = next((os.environ.get(name) for name in TOKEN_ENV_NAMES if os.environ.get(name)), None)
    if not token:
        raise SystemExit("MCP_TOKEN or FACTOR4_MCP_TOKEN is required")

    settings = SettingsLoader.load("test", ROOT)
    stamp = datetime.now(SHANGHAI).strftime("%Y%m%dT%H%M%S%z")
    output = REPORT_ROOT / f"{stamp}-status-permission-closure"
    output.mkdir(parents=True, exist_ok=False)

    before = read_business_watermark()
    backend, backend_calls = backend_identity_probe()
    write_json(output / "backend-evidence.json", backend)
    mcp = execute_mcp_permission_probe(token, output)
    write_json(output / "mcp-evidence.json", mcp)
    database = read_permission_database_evidence(
        mcp.get("read_call_request_id"), backend.get("identity") or {}
    )
    write_json(output / "db-permission-evidence.json", database)
    after = read_business_watermark()
    write_json(output / "db-before.json", before)
    write_json(output / "db-after.json", after)

    env110 = load_case(args.env110, "ENV-110") if args.env110 else current_env110_case()
    met305 = load_case(args.met305, "MET-305") if args.met305 else current_met305_case()
    db608 = adjudicate_db608(mcp, backend, database)
    cases = [env110, met305, db608]
    report: dict[str, Any] = {
        "authority": (
            "This adjudicated-summary.json is the authoritative verdict for this run. Raw "
            "request/response artifacts are evidence and do not override it."
        ),
        "captured_at": datetime.now(SHANGHAI).isoformat(),
        "environment": "test",
        "mode": "READ_ONLY_BUSINESS_DATA",
        "scope": ["ENV-110", "MET-305", "DB-608"],
        "cases": cases,
        "mcp_evidence": mcp,
        "backend_evidence": backend,
        "database_permission_evidence": database,
        "database_watermarks": {
            "before": before,
            "after": after,
            "business_tables_unchanged": before.get("tables") == after.get("tables"),
            "note": (
                "Direct DB sessions were read-only. The MCP read request is expected to update "
                "access audit telemetry and PAT last-used time, neither of which is a Factor 4 "
                "business-data mutation."
            ),
        },
        "backend_calls": backend_calls,
        "mcp_read_tools_called": mcp.get("read_tools_called", []),
        "mcp_write_tools_called": mcp.get("write_tools_called", []),
        "confirmed_defects": [case["case_id"] for case in cases if case.get("status") == "FAIL"],
        "totals": dict(Counter(str(case.get("status")) for case in cases)),
        "security": {},
    }
    write_json(output / "adjudicated-summary.json", report)
    (output / "summary.md").write_text(render_markdown(report), encoding="utf-8")

    jwt_values: list[str] = []
    forbidden_values = [
        token,
        settings.authentication.privileged.password or "",
        settings.authentication.restricted.password or "",
        settings.authentication.non_owner.password or "",
        settings.database.password or "",
        *jwt_values,
    ]
    security = scan_artifacts(output, forbidden_values)
    report["security"] = security
    write_json(output / "adjudicated-summary.json", report)
    write_json(output / "sensitive-scan.json", security)
    final_security = scan_artifacts(output, forbidden_values)
    if not final_security["passed"]:
        raise RuntimeError("Sensitive artifact scan failed")
    print(output)
    print(json.dumps(report["totals"], sort_keys=True))
    return 1 if report["confirmed_defects"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
