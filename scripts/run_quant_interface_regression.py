"""Read-only fresh regression for the quantitative-trading API surface."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
import yaml

# Keep direct ``python scripts/...`` execution bound to this checkout after a
# workspace move, rather than an older editable installation of ``service``.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.strategy_dashboard_strategy_scope import (
    strategy_overview_differences,
    strategy_overview_scopes,
)

METRICS = (
    "margin_equity,wallet_balance,realized_pnl_gross,unrealized_pnl,cumulative_return,"
    "current_drawdown,commission_expense,funding_pnl,annualized_return,max_drawdown,"
    "sharpe_ratio,calmar_ratio,position_utilization,sortino_ratio,volatility,win_rate,profit_loss_ratio"
)
SERIES_TYPES = (
    "performance_nav,drawdown,realized_pnl_gross,unrealized_pnl,cumulative_return,"
    "margin_equity,annualized_return,commission_expense,funding_pnl"
)
TRADING_ENDPOINTS = ("positions", "signals", "signal-batches", "rebalance-plans", "order-intents", "orders", "fills", "cash-flows")
TIME_ENDPOINTS = {"signals", "signal-batches", "rebalance-plans", "orders", "fills"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if re.search(r"password|secret|token|authorization|api.?key", key, re.I) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"Bearer\s+[A-Za-z0-9_.-]+|naf_mcp_[A-Za-z0-9_-]+", "[REDACTED]", value)
    return value


def data_obj(body: Any) -> dict[str, Any] | None:
    data = body.get("data") if isinstance(body, dict) else None
    return data if isinstance(data, dict) else None


def items_of(body: Any) -> list[dict[str, Any]]:
    data = data_obj(body)
    items = data.get("items") if data else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def walk_values(value: Any, path: str = ""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from walk_values(item, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_values(item, f"{path}[{index}]")
    else:
        yield path, value


def finite_number(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(value)
    if isinstance(value, str):
        if value.strip().lower() in {"nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}:
            return False
        try:
            math.isfinite(float(value))
        except ValueError:
            return True
    return True


class Regression:
    def __init__(self, base_url: str, credentials: dict[str, str], output: Path) -> None:
        self.base_url = base_url.rstrip("/")
        self.output = output
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "X-Quant-Contract": "2", "Cache-Control": "no-cache, no-store, max-age=0", "Pragma": "no-cache"})
        proxy = os.environ.get("QUEST_FORMULA_HTTP_PROXY")
        if proxy:
            self.session.trust_env = False
            self.session.proxies = {"http": proxy, "https": proxy}
        self.credentials = credentials
        self.results: list[dict[str, Any]] = []
        self.findings: list[dict[str, Any]] = []
        self.window_to = utc_now()
        self.window_from = self.window_to - timedelta(days=45)

    def login(self) -> None:
        response = None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.session.post(self.base_url + "/auth/login", json={"email": self.credentials["email"], "password": self.credentials["password"]}, timeout=45)
                break
            except requests.RequestException as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1.0 * (attempt + 1))
        if response is None:
            raise RuntimeError(f"login request failed: {type(last_error).__name__}: {last_error}") from last_error
        if response.status_code != 200:
            raise RuntimeError(f"login failed: HTTP {response.status_code}")
        token = response.json().get("data", {}).get("token")
        if not token:
            raise RuntimeError("login response does not contain data.token")
        self.session.headers["Authorization"] = f"Bearer {token}"

    def request(self, name: str, method: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        started = time.monotonic()
        headers = {"X-Request-ID": str(uuid.uuid4()), "Cache-Control": "no-cache, no-store, max-age=0", "Pragma": "no-cache"}
        response = None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.session.request(method, self.base_url + path, params=params, headers=headers, timeout=60)
                break
            except requests.RequestException as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        if response is not None:
            try:
                body = response.json()
            except ValueError:
                body = {"non_json": response.text[:1000]}
            entry = {
                "name": name, "method": method, "path": path, "params": dict(params or {}),
                "status": response.status_code, "elapsed_seconds": round(time.monotonic() - started, 3),
                "response_headers": {key.lower(): value for key, value in response.headers.items() if key.lower() in {"cache-control", "age", "etag", "x-cache", "content-type"}},
                "body": redact(body),
            }
        else:
            exc = last_error
            entry = {"name": name, "method": method, "path": path, "params": dict(params or {}), "status": None, "elapsed_seconds": round(time.monotonic() - started, 3), "error": type(exc).__name__, "error_message": str(exc)[:300]}
        self.results.append(entry)
        return entry

    def add_finding(self, kind: str, title: str, detail: str, evidence: list[str]) -> None:
        self.findings.append({"kind": kind, "title": title, "detail": detail, "evidence": evidence})

    def check_entry(self, entry: dict[str, Any]) -> None:
        status, name, body = entry.get("status"), entry["name"], entry.get("body")
        if status is None:
            self.add_finding("BLOCKED", "接口请求网络异常", f"{name} 未取得 HTTP 响应：{entry.get('error_message', entry.get('error'))}", [name])
            return
        error_code = body.get("error", {}).get("code") if isinstance(body, dict) and isinstance(body.get("error"), dict) else None
        # A same-name overview is allowed to span exchanges; ambiguity must not
        # be accepted as a valid refusal of that overview request.
        expected_business_errors = {"HISTORY_NOT_SUPPORTED"}
        if status >= 500:
            self.add_finding("BUG_CANDIDATE", "接口返回服务端错误", f"{name} 返回 HTTP {status}", [name])
        elif status >= 400:
            if error_code in expected_business_errors:
                self.add_finding("DATA_GAP", "接口按业务规则拒绝当前样本", f"{name} 返回 HTTP {status}，reason={error_code}: {body.get('error', {}).get('message')}", [name])
            else:
                self.add_finding("BUG_CANDIDATE", "接口有效请求返回客户端错误", f"{name} 返回 HTTP {status}，参数来自当前前端请求形状", [name])
        if not isinstance(body, dict):
            self.add_finding("BUG_CANDIDATE", "接口未返回 JSON 对象", f"{name} 的响应不是 JSON 对象", [name])
            return
        if status and 200 <= status < 300:
            if body.get("success") is False:
                self.add_finding("BUG_CANDIDATE", "HTTP 200 但业务 success=false", f"{name} 在 HTTP 200 时返回 success=false", [name])
            if "data" not in body:
                self.add_finding("BUG_CANDIDATE", "成功响应缺少 data", f"{name} 的 2xx 响应缺少 data 字段", [name])
            meta = body.get("meta")
            if not isinstance(meta, dict):
                self.add_finding("OBSERVATION", "成功响应缺少统一 meta", f"{name} 未返回 meta 对象，无法核对快照和数据状态", [name])
            elif meta.get("data_status") in {"partial", "unavailable"}:
                self.add_finding("DATA_GAP", "接口明确返回部分或不可用数据", f"{name}: data_status={meta.get('data_status')}, reason_code={meta.get('reason_code')}, issues={meta.get('issues')}", [name])
        for path, value in walk_values(body):
            if not finite_number(value):
                self.add_finding("BUG_CANDIDATE", "接口返回非有限数值", f"{name} 的 {path} 为 {value!r}", [name])

    def call(self, name: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        entry = self.request(name, "GET", path, params)
        self.check_entry(entry)
        return entry

    def check_pagination(self, entry: dict[str, Any]) -> None:
        data = data_obj(entry.get("body"))
        if not data:
            return
        if data.get("has_more") is True and not (data.get("next_cursor") or data.get("page") or data.get("page_info")):
            self.add_finding("BUG_CANDIDATE", "分页声明有下一页但未返回定位信息", f"{entry['name']} has_more=true 且没有 next_cursor/page/page_info", [entry["name"]])
        ids = []
        for item in data.get("items", []) if isinstance(data.get("items"), list) else []:
            if entry["path"] == "/quantitative-trading/strategies" and item.get("strategy_definition_id") is not None:
                # Strategy management is one row per strategy-account relation;
                # the same definition/exchange may legitimately repeat for different accounts.
                account_ids = item.get("account_ids")
                account_id = account_ids[0] if isinstance(account_ids, list) and account_ids else item.get("account_id")
                ids.append((str(item["strategy_definition_id"]), str(item.get("exchange") or ""), str(account_id or "")))
                continue
            identity = item.get("id") or item.get("assignment_id") or item.get("order_id") or item.get("fill_id") or item.get("signal_id") or item.get("bill_id") or item.get("account_id") or item.get("strategy_definition_id")
            if identity is not None:
                ids.append(str(identity))
        if len(ids) != len(set(ids)):
            self.add_finding("BUG_CANDIDATE", "单页结果存在重复业务 ID", f"{entry['name']} 返回重复 ID: {ids}", [entry["name"]])

    def discover(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        common = {"population": "all", "scope": "lifetime", "metric_basis": "accrual", "metric_source": "prometheus", "metrics": METRICS, "metric_from": iso(self.window_from), "metric_to": iso(self.window_to), "limit": 100}
        strategies_entry = self.call("strategies", "/quantitative-trading/strategies", common)
        accounts_entry = self.call("accounts", "/admin/quant-accounts", {"population": "all", "scope": "lifetime", "metric_basis": "accrual", "metrics": METRICS, "pagination": "page", "page": 1, "limit": 100})
        strategies, accounts = items_of(strategies_entry.get("body")), items_of(accounts_entry.get("body"))
        if not strategies:
            self.add_finding("DATA_GAP", "策略目录没有可用样本", "当前策略列表为空，后续策略维度接口无法覆盖", ["strategies"])
        if not accounts:
            self.add_finding("DATA_GAP", "账号目录没有可用样本", "当前账号列表为空，后续账号维度接口无法覆盖", ["accounts"])
        return strategies, accounts

    def run(self) -> None:
        strategies, accounts = self.discover()
        self.check_pagination(self.results[-2])
        self.check_pagination(self.results[-1])
        for kind in ("exchanges", "researchers"):
            self.check_pagination(self.call(f"options-{kind}", "/quantitative-trading/options", {"kind": kind, "limit": 100}))
        all_account_ids = list(dict.fromkeys(str(item.get("account_id") or item.get("id")) for item in accounts if item.get("account_id") is not None or item.get("id") is not None))
        account_ids = [account_id for account_id in all_account_ids if account_id.isdigit()]
        for account_id in all_account_ids:
            if not account_id.isdigit():
                self.add_finding("DATA_GAP", "目录包含不可用于账号详情接口的外部账号标识", f"目录返回 account_id={account_id!r}，详情与交易端点只接受数字 local_account_id，已跳过该样本", ["accounts"])
        # Every stable ID remains an entry point to a same-name overview. The
        # clicked list row's exchange must not narrow any detail section.
        strategy_ids = list(dict.fromkeys(
            ident for scope in strategy_overview_scopes(strategies)
            for ident in scope.definition_ids
        ))
        for account_id in account_ids:
            self.call(f"account-{account_id}-detail", f"/admin/quant-accounts/{account_id}")
            self.check_pagination(self.call(f"account-{account_id}-assignments", f"/quantitative-trading/accounts/{account_id}/assignments", {"status": "all", "from": iso(self.window_from), "to": iso(self.window_to), "scope": "lifetime", "metric_basis": "accrual", "metric_source": "prometheus", "limit": 100}))
        for strategy_id in strategy_ids:
            prefix = f"strategy-{strategy_id}-overview"
            self.call(prefix + "-detail", f"/quantitative-trading/strategy-definitions/{strategy_id}", {"metric_source": "prometheus"})
            self.check_pagination(self.call(prefix + "-accounts", f"/quantitative-trading/strategy-definitions/{strategy_id}/accounts", {"assignment_status": "all", "metric_source": "prometheus", "scope": "lifetime", "metric_basis": "accrual", "from": iso(self.window_from), "to": iso(self.window_to), "limit": 100}))

        subjects: list[tuple[str, str]] = [("portfolio", "")] + [("account", ident) for ident in account_ids] + [("strategy", ident) for ident in strategy_ids]
        for subject_type, subject_id in subjects:
            base: dict[str, Any] = {"subject_type": subject_type, "scope": "lifetime", "metric_basis": "accrual", "population": "all", "from": iso(self.window_from), "to": iso(self.window_to), "metric_source": "prometheus"}
            if subject_id:
                base["subject_id"] = subject_id
            if subject_type == "strategy":
                base["aggregation"] = "linked_accounts"
            prefix = f"{subject_type}-{subject_id or 'portfolio'}"
            if subject_type == "strategy":
                prefix += "-overview"
            self.call(prefix + "-summary", "/quantitative-trading/analytics/summary", base | {"metrics": METRICS})
            self.call(prefix + "-risk", "/quantitative-trading/analytics/risk", base)
            self.call(prefix + "-series", "/quantitative-trading/analytics/series", base | {"types": SERIES_TYPES, "granularity": "1d", "include_markers": "true"})
            if subject_type != "portfolio":
                position_base = {key: value for key, value in base.items() if subject_type != "account" or key != "metric_source"}
                self.call(prefix + "-position-series", "/quantitative-trading/analytics/series", position_base | {"types": "position_unrealized_pnl", "granularity": "1d", "include_markers": "true"})
            if subject_type == "portfolio":
                self.call(prefix + "-contributions", "/quantitative-trading/analytics/contributions", base | {"selection_kind": "strategy", "denominator_scope": "filtered"})

        jobs: list[tuple[str, str, dict[str, Any]]] = []
        for subject_type, subject_id in [("account", ident) for ident in account_ids] + [("strategy", ident) for ident in strategy_ids]:
            endpoints = tuple(endpoint for endpoint in TRADING_ENDPOINTS if endpoint != "cash-flows" or subject_type == "account")
            for endpoint in endpoints:
                params: dict[str, Any] = {"subject_type": subject_type, "subject_id": subject_id, "limit": 100}
                if subject_type == "strategy" and endpoint == "positions":
                    params.update({"metric_source": "prometheus", "aggregation": "linked_accounts"})
                if endpoint in TIME_ENDPOINTS:
                    params.update({"from": iso(self.window_from), "to": iso(self.window_to)})
                prefix = f"{subject_type}-{subject_id}" + ("-overview" if subject_type == "strategy" else "")
                jobs.append((f"{prefix}-{endpoint}", f"/quantitative-trading/trading/{endpoint}", params))
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(self.call, name, path, params) for name, path, params in jobs]
            for future in as_completed(futures):
                future.result()
        for entry in self.results:
            if "/trading/" in entry["path"]:
                self.check_pagination(entry)

        bills = self.call("profit-sharing-bills", "/quantitative-trading/profit-sharing/bills", {"from": iso(self.window_from), "to": iso(self.window_to), "limit": 100})
        self.check_pagination(bills)
        for item in items_of(bills.get("body")):
            bill_id = item.get("bill_id") or item.get("id")
            if bill_id:
                self.call(f"bill-{bill_id}-detail", f"/quantitative-trading/profit-sharing/bills/{bill_id}", {"receipt_limit": 100, "notice_limit": 100})
        for account_id in account_ids:
            self.check_pagination(self.call(f"profit-sharing-bills-account-{account_id}", "/quantitative-trading/profit-sharing/bills", {"account_id": account_id, "limit": 100}))
        self.check_cross_field_invariants()
        self.check_identity_consistency()

    def check_cross_field_invariants(self) -> None:
        for entry in self.results:
            data = data_obj(entry.get("body"))
            exposure = data.get("exposure_summary") if data else None
            if not isinstance(exposure, dict):
                continue
            def dec(key: str) -> float | None:
                value = exposure.get(key, {}).get("value") if isinstance(exposure.get(key), dict) else None
                try:
                    return float(value) if value is not None else None
                except (TypeError, ValueError):
                    return None
            long_value, short_value, total_value, net_value = (dec(k) for k in ("long_notional", "short_notional", "total_notional", "net_exposure_amount"))
            if None not in (long_value, short_value, total_value) and abs((long_value + short_value) - total_value) > 1e-6:
                self.add_finding("BUG_CANDIDATE", "持仓敞口汇总不守恒", f"{entry['name']} long+short={long_value + short_value} != total={total_value}", [entry["name"]])
            if None not in (long_value, short_value, net_value) and abs((long_value - short_value) - net_value) > 1e-6:
                self.add_finding("BUG_CANDIDATE", "持仓净敞口汇总不守恒", f"{entry['name']} long-short={long_value - short_value} != net={net_value}", [entry["name"]])
            equity, used, utilization = dec("margin_equity"), dec("margin_used"), dec("margin_utilization")
            if None not in (equity, used, utilization) and equity and abs(used / equity - utilization) > 2e-6:
                self.add_finding("BUG_CANDIDATE", "保证金使用率与金额不一致", f"{entry['name']} expected={used / equity:.10f}, actual={utilization:.10f}", [entry["name"]])

    def check_identity_consistency(self) -> None:
        """Compare a same-name overview against full, unfiltered list membership.

        An exchange/account row is not the detail population; mixed status does
        not imply a mismatch. An incomplete directory cannot prove membership.
        """
        strategies_entry = next((item for item in self.results if item["name"] == "strategies"), None)
        if not strategies_entry:
            return
        catalog = data_obj(strategies_entry.get("body")) or {}
        selection = strategies_entry.get("params") or {}
        filtered = any(selection.get(field) for field in (
            "search", "strategy_search", "exchange", "strategy_exchange",
            "runtime_status", "display_status", "researcher_user_id", "account_ids",
            "strategy_definition_ids",
        )) or selection.get("population") not in (None, "all")
        if catalog.get("has_more") is True or (catalog.get("page_info") or {}).get("has_more") is True or filtered:
            self.add_finding("DATA_GAP", "策略总览缺少完整未筛选目录",
                             "当前目录为分页片段或筛选子集，不能用它判定跨交易所总览漏算/多算", ["strategies"])
            return
        for scope in strategy_overview_scopes(items_of(strategies_entry.get("body"))):
            for strategy_id in scope.definition_ids:
                detail_name = f"strategy-{strategy_id}-overview-detail"
                entry = next((item for item in self.results if item["name"] == detail_name), None)
                if not entry or entry.get("status") != 200:
                    continue
                detail = (data_obj(entry.get("body")) or {}).get("strategy")
                if not isinstance(detail, dict):
                    self.add_finding("DATA_GAP", "策略详情缺少可核对的策略对象", detail_name, [detail_name])
                    continue
                for difference in strategy_overview_differences(scope, detail):
                    self.add_finding(difference["kind"], difference["title"], difference["detail"],
                                     ["strategies", detail_name])

    def write_report(self) -> Path:
        self.output.mkdir(parents=True, exist_ok=True)
        status_values = sorted({item.get("status") for item in self.results}, key=lambda value: str(value))
        payload = {"environment": "test", "base_url": self.base_url, "observed_at": utc_now().isoformat(), "window": {"from": iso(self.window_from), "to": iso(self.window_to)}, "request_count": len(self.results), "status_counts": {str(code): sum(1 for item in self.results if item.get("status") == code) for code in status_values}, "findings": self.findings, "requests": self.results}
        json_path = self.output / "interface-regression.json"
        json_path.write_text(json.dumps(redact(payload), ensure_ascii=False, indent=2), encoding="utf-8")
        lines = ["# 量化交易接口全面回归", "", f"- 环境：test；观察时间：{payload['observed_at']}", f"- 请求数：{len(self.results)}", f"- 状态码：{payload['status_counts']}", "", "## 真实问题候选", ""]
        bug_findings = [finding for finding in self.findings if finding["kind"] == "BUG_CANDIDATE"]
        if not bug_findings:
            lines.append("未发现接口层断言失败。")
        else:
            for index, finding in enumerate(bug_findings, start=1):
                lines += [f"### {index}. {finding['title']}", "", finding["detail"], "", f"证据：{', '.join(finding['evidence'])}", ""]
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for finding in self.findings:
            if finding["kind"] != "BUG_CANDIDATE":
                grouped[(finding["kind"], finding["title"])].append(finding)
        lines += ["## 数据缺口、业务不可用和阻塞", ""]
        for (kind, title), findings in sorted(grouped.items()):
            evidence = []
            for finding in findings:
                evidence.extend(finding["evidence"])
            unique_evidence = list(dict.fromkeys(evidence))
            lines += [f"### [{kind}] {title}", "", f"出现 {len(findings)} 次；涉及接口：{', '.join(unique_evidence[:20])}" + (" ..." if len(unique_evidence) > 20 else ""), "", findings[0]["detail"][:1200], ""]
        lines += ["## 请求明细", "", "完整响应见 interface-regression.json。", ""]
        (self.output / "interface-regression.md").write_text("\n".join(lines), encoding="utf-8")
        return json_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/test.yaml")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    stamp = utc_now().strftime("%Y%m%d-%H%M%S")
    output = Path(args.output) if args.output else Path("reports") / f"quant-api-regression-{stamp}"
    credentials = config["authentication"]
    regression = Regression(config["api"]["base_url"], {"email": credentials["privileged_email"], "password": credentials["privileged_password"]}, output)
    regression.login()
    regression.run()
    path = regression.write_report()
    status_values = sorted({item.get("status") for item in regression.results}, key=lambda value: str(value))
    print(json.dumps({"report": str(path), "request_count": len(regression.results), "status_counts": {str(code): sum(1 for item in regression.results if item.get("status") == code) for code in status_values}, "finding_count": len(regression.findings)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
