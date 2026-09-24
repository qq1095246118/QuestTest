"""Execute a fresh, bounded read-only supplement to the quant API regression.

This operational runner discovers every identity during this invocation. It does
not turn request counts into business-case counts, or perform financial actions.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from uuid import uuid4

from config.settings import SettingsLoader
from scripts.run_quant_interface_regression import Regression, METRICS, SERIES_TYPES, data_obj, items_of, redact


class Supplement(Regression):
    """Retain evidence incrementally while reusing the existing read API client."""

    def __init__(self, base_url: str, credentials: dict[str, str], output: Path) -> None:
        """Initialize evidence collections; no HTTP request is made."""
        super().__init__(base_url, credentials, output)
        output.mkdir(parents=True, exist_ok=True)
        self.checks: list[dict[str, Any]] = []
        self.session.trust_env = False
        self.session.proxies = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
        self.session.headers["X-Quant-Contract"] = "2"

    def request(self, name: str, method: str, path: str,
                params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Save each non-mutating business request immediately; failures persist."""
        if method != "GET":
            raise ValueError("Supplement only permits business GET requests")
        entry = super().request(name, method, path, params)
        (self.output / (name + ".json")).write_text(json.dumps(redact(entry), ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"name": name, "status": entry.get("status")}, ensure_ascii=False), flush=True)
        return entry

    def save_check(self, name: str, passed: bool | None, **details: Any) -> None:
        """Record a concrete assertion or an explicit unresolved sample gap."""
        self.checks.append({"name": name, "passed": passed, **details})

    def full_pages(self, name: str, path: str, params: dict[str, Any], *,
                   identity_key: str | tuple[str, ...], numbered: bool = False) -> list[dict[str, Any]]:
        """Read at most 200 fixed-size pages from one snapshot; require identities."""
        base = params | {"limit": 1}
        if numbered:
            base.update({"pagination": "page", "page": 1})
        query = dict(base)
        records: list[dict[str, Any]] = []
        snapshot = None
        total = None
        for page in range(1, 201):
            entry = self.call(f"{name}-page-{page}", path, query)
            if entry.get("status") != 200:
                self.save_check(name, None, reason="HTTP_REQUEST_NOT_COMPLETED", request=entry["name"])
                return records
            body = entry["body"]
            data = data_obj(body) or {}
            current_snapshot = (body.get("meta") or {}).get("snapshot_id")
            if page == 1:
                snapshot, total = current_snapshot, data.get("total")
            elif current_snapshot != snapshot:
                self.save_check(name, False, reason="SNAPSHOT_CHANGED", expected=snapshot, actual=current_snapshot)
                return records
            records.extend(items_of(body))
            has_more = data.get("has_more", (data.get("page_info") or {}).get("has_more"))
            if has_more is not True:
                fields = (identity_key,) if isinstance(identity_key, str) else identity_key
                ids = ["|".join(str(row[key]) for key in fields) for row in records
                       if all(row.get(key) is not None for key in fields)]
                exact = data.get("total_status") == "exact"
                passed = len(ids) == len(records) == len(set(ids)) and (not exact or total == len(records))
                self.save_check(name, passed, count=len(records), ids=ids, total=total,
                                total_status=data.get("total_status"), pages=page, snapshot_id=snapshot)
                return records
            if not snapshot:
                self.save_check(name, None, reason="MISSING_PAGINATION_SNAPSHOT", count=len(records))
                return records
            query = base | {"snapshot_id": snapshot}
            if numbered:
                query["page"] = page + 1
            elif data.get("next_cursor"):
                query["cursor"] = data["next_cursor"]
            else:
                self.save_check(name, False, reason="HAS_MORE_WITHOUT_CURSOR")
                return records
        self.save_check(name, None, reason="BOUNDED_PAGE_LIMIT", count=len(records))
        return records

    def check_filters(self, accounts: list[dict[str, Any]], strategies: list[dict[str, Any]]) -> None:
        """Verify documented status/exchange/search filters with freshly found identities."""
        for kind, path, rows, id_key in (
            ("accounts", "/admin/quant-accounts", accounts, "account_id"),
            ("strategies", "/quantitative-trading/strategies", strategies, "strategy_definition_id"),
        ):
            base: dict[str, Any] = {"population": "all", "scope": "lifetime", "metric_basis": "accrual", "limit": 100}
            if kind == "accounts":
                base.update({"pagination": "page", "page": 1})
            else:
                base["metric_source"] = "prometheus"
            for state in ("running", "stopped"):
                name = f"filter-{kind}-{state}"
                entry = self.call(name, path, base | {"runtime_status": state})
                if entry.get("status") == 200:
                    items = items_of(entry["body"])
                    status_field = "actual_status" if kind == "accounts" else "runtime_status"
                    self.save_check(name, all(item.get(status_field) == state for item in items),
                                    count=len(items), status_field=status_field,
                                    statuses=[item.get(status_field) for item in items])
            if rows:
                sample = next((row for row in rows if row.get("code") or row.get("account_name")), None)
                if sample:
                    search = str(sample.get("code") or sample.get("account_name"))
                    name = f"filter-{kind}-name"
                    entry = self.call(name, path, base | {"search": search})
                    if entry.get("status") == 200:
                        ids = [str(row.get(id_key)) for row in items_of(entry["body"])]
                        self.save_check(name, str(sample[id_key]) in ids, search=search, expected_id=str(sample[id_key]), actual_ids=ids)
            if kind == "accounts":
                exchange = next((str(row["exchange"]) for row in rows if row.get("exchange")), None)
                if exchange:
                    name = "filter-accounts-exchange"
                    entry = self.call(name, path, base | {"exchange": exchange})
                    if entry.get("status") == 200:
                        values = [str(row.get("exchange", "")) for row in items_of(entry["body"])]
                        self.save_check(name, bool(values) and all(value.casefold() == exchange.casefold() for value in values), expected=exchange, actual=values)

    def receipt_pages_and_evidence(self, bills: list[dict[str, Any]]) -> None:
        """Read receipt pages and existing attachments; never register or approve a receipt."""
        for bill in bills:
            bill_id = str(bill.get("bill_id") or bill.get("id") or "")
            if not bill_id:
                continue
            path = "/quantitative-trading/profit-sharing/bills/" + quote(bill_id, safe="")
            params: dict[str, Any] = {"receipt_limit": 1, "notice_limit": 1}
            snapshot = None
            receipts: list[dict[str, Any]] = []
            totals = None
            total = None
            for page in range(1, 101):
                entry = self.call(f"bill-{bill_id}-receipt-page-{page}", path, params)
                if entry.get("status") != 200:
                    self.save_check("receipt-pages-" + bill_id, None, reason="HTTP_REQUEST_NOT_COMPLETED")
                    break
                body, data = entry["body"], data_obj(entry["body"]) or {}
                current_snapshot = body.get("meta", {}).get("snapshot_id")
                subset = data.get("receipts") or {}
                amounts = {key: data.get(key) for key in ("receivable_amount", "received_amount", "outstanding_amount", "registered_received_amount", "pending_received_amount")}
                if page == 1:
                    snapshot, totals, total = current_snapshot, amounts, subset.get("total")
                elif current_snapshot != snapshot or amounts != totals:
                    self.save_check("receipt-pages-" + bill_id, False, reason="SNAPSHOT_OR_FULL_TOTALS_CHANGED")
                    break
                receipts.extend(subset.get("items") or [])
                if not subset.get("has_more"):
                    ids = [str(row["receipt_id"]) for row in receipts if row.get("receipt_id")]
                    self.save_check("receipt-pages-" + bill_id, len(ids) == len(receipts) == len(set(ids)) and (total is None or total == len(ids)),
                                    receipt_count=len(receipts), total=total, pages=page, snapshot_id=snapshot)
                    break
                if not subset.get("next_cursor") or not snapshot:
                    self.save_check("receipt-pages-" + bill_id, False, reason="MISSING_PAGE_METADATA")
                    break
                params = {"receipt_limit": 1, "notice_limit": 1, "receipt_cursor": subset["next_cursor"], "snapshot_id": snapshot}
            else:
                self.save_check("receipt-pages-" + bill_id, None, reason="BOUNDED_PAGE_LIMIT")
            for receipt in receipts:
                for reference in receipt.get("evidence_refs") or []:
                    attachment = path + "/receipts/" + quote(str(receipt["receipt_id"]), safe="") + "/evidence/" + quote(str(reference), safe="")
                    response = self.session.get(self.base_url + attachment, timeout=60, allow_redirects=False,
                                                headers={"X-Request-ID": str(uuid4())})
                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
                    raw = response.content
                    recognized = ((content_type in {"image/jpeg", "image/jpg"} and raw.startswith(b"\xff\xd8\xff")) or
                                  (content_type == "image/png" and raw.startswith(b"\x89PNG\r\n\x1a\n")) or
                                  (content_type == "application/pdf" and raw.startswith(b"%PDF")))
                    evidence = {"name": "receipt-evidence-" + str(reference), "method": "GET", "path": attachment,
                                "params": {}, "status": response.status_code, "binary": True,
                                "content_type": content_type, "byte_count": len(raw), "sha256": sha256(raw).hexdigest()}
                    self.results.append(evidence)
                    (self.output / (evidence["name"] + ".json")).write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
                    passed = response.status_code == 200 and bool(raw) and recognized
                    if response.status_code == 200 and raw and content_type not in {"image/jpeg", "image/jpg", "image/png", "application/pdf"}:
                        passed = None
                    self.save_check("receipt-evidence-" + str(reference), passed,
                                    http_status=response.status_code, content_type=content_type,
                                    byte_count=len(raw), sha256=sha256(raw).hexdigest(), path=attachment)

    def execute(self) -> None:
        """Run the explicitly read-only supplementary matrix after authentication."""
        common = {"population": "all", "scope": "lifetime", "metric_basis": "accrual"}
        accounts = self.full_pages("accounts", "/admin/quant-accounts", common, identity_key="account_id", numbered=True)
        if self.checks[-1].get("passed") is None:
            # A live source refresh may legitimately expire the first snapshot.
            # Start from a new first page; never append a new generation to it.
            accounts = self.full_pages("accounts-fresh-retry", "/admin/quant-accounts", common, identity_key="account_id", numbered=True)
        strategies = self.full_pages("strategies", "/quantitative-trading/strategies", common | {"metric_source": "prometheus"}, identity_key=("strategy_definition_id", "exchange"))
        self.check_filters(accounts, strategies)
        metrics = METRICS
        for account in accounts:
            ident = str(account.get("account_id", ""))
            if not ident.isdigit():
                continue
            base = {"subject_type": "account", "subject_id": ident, "scope": "cycle", "metric_basis": "display", "population": "running"}
            for endpoint in ("summary", "risk", "series"):
                query = base | ({"metrics": metrics} if endpoint == "summary" else
                                {"types": SERIES_TYPES, "granularity": "1d", "include_markers": "true"} if endpoint == "series" else {})
                self.call(f"current-cycle-account-{ident}-{endpoint}", "/quantitative-trading/analytics/" + endpoint, query)
        for kind in ("business_owners", "chains", "nt_accounts", "nt_strategies"):
            self.call("options-" + kind, "/quantitative-trading/options", {"kind": kind, "limit": 100})
        bills = self.full_pages("bills", "/quantitative-trading/profit-sharing/bills", {}, identity_key="bill_id")
        self.receipt_pages_and_evidence(bills)


def main() -> int:
    """Authenticate to configured test only, execute, and always retain a summary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = SettingsLoader.load("test")
    if settings.environment != "test" or urlparse(settings.api.base_url).hostname not in settings.environment_safety.allowed_hosts:
        raise RuntimeError("BLOCKED_ENV")
    credentials = settings.authentication.privileged
    runner = Supplement(settings.api.base_url, {"email": credentials.email, "password": credentials.password}, args.output)
    started = datetime.now(timezone.utc).isoformat()
    error = None
    try:
        runner.login()
        runner.execute()
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": str(exc)[:400]}
        raise
    finally:
        runner.session.close()
        runner.write_report()
        summary = {"started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
                   "business_get_requests": len(runner.results), "checks": runner.checks,
                   "check_status_counts": dict(Counter("passed" if item["passed"] is True else "failed" if item["passed"] is False else "gap" for item in runner.checks)),
                   "error": redact(error), "scope": "Fresh supplementary reads; no financial actions or DB writes."}
        (args.output / "supplementary-checks.json").write_text(json.dumps(redact(summary), ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in summary.items() if key != "checks"}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
