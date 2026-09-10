"""Catalog formula, schema and persisted-result business reconciliation."""

from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from api.factor4_formula_api import Factor4FormulaAPI
from api.factor4_summary_api import Factor4SummaryAPI
from db.factor4_formula_repository import Factor4FormulaRepository, FormulaCatalogSnapshot
from db.factor4_read_repository import SUMMARY_KEYS
from service.factor4_calculation_service import (
    FormulaOffsetError, formula_dependency_offsets, _is_correct_dpo,
    _is_correct_fixed_horizon, _is_iv_rv_difference, _formula_candidate, _expressions_equivalent,
)
from service.factor4_read_service import ReadCheck, ReadContractError, ReadPrecondition, read_tool_body, read_tool_page
from service.factor4_summary_service import compare_summary, summary_scope

_CONTROL = frozenset({"window", "min_periods", "True", "False", "None", "nan", "inf", "np", "pd"})
_DERIVED = frozenset({"returns", "log_return", "log_returns", "vwap", "truerange", "rsi", "taker_volume", "buy", "sell"})
_KNOWN_FAMILIES = ("topup", "funding", "long_short", "vwap", "iv_rv", "implied_vol", "breakout", "oi_momentum_corr")
_IDENTITY = ("run_id", "calculation_mode", "factor_bar_interval", "factor_window_bars", "return_bar_interval", "forward_return_bars")
_CANDIDATE_CATEGORIES = frozenset({
    "empty_formula", "syntax", "unresolved_inputs", "params_only_input_miss", "future_target",
    "annualization", "interval_mismatch", "dataset_frequency", "field_frequency", "negative_temporal",
    "center_or_backfill", "unbounded_aggregate", "temporal_constants", "independent_temporal_family",
    "fixed_formula_family",
})


def _json(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _mapping(value: Any) -> dict[str, Any]:
    value = _json(value)
    return value if isinstance(value, dict) else {}


def _fields(value: Any) -> set[str]:
    value = _json(value)
    return {str(item) for item in value} if isinstance(value, list) else set()


def _identity(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (int(row["factor_id"]), bool(row["is_sub_factor_id"]), *(row.get(key) for key in _IDENTITY))


def _ref(row: Mapping[str, Any]) -> str:
    return f"{'sub_factor' if row.get('is_sub_factor_id', 1) else 'factor'}:{row['factor_id']}"


def compare_exact_formula_output(row: Mapping[str, Any], data: Mapping[str, Any]) -> ReadCheck:
    """Compare an already fetched formula with persisted exact-Run evidence.

    Inputs are the independent DB row and public response data. Returns field-level
    differences without I/O or mathematical evaluation; missing DB identity raises
    KeyError. Publicly missing fields are differences, including expected nulls.
    """
    issues, ref = [], _ref(row)
    if data.get("factor_ref") != ref:
        issues.append(f"{ref}:formula:factor_ref")
    for key in ("run_id", "expression", "formula_hash", "formula_version", "source_detail_id", "required_fields"):
        if key not in data or _json(data.get(key)) != _json(row.get(key)):
            issues.append(f"{ref}:formula:{key}")
    identity = _mapping(data.get("metric_identity"))
    for key in ("calculation_mode", "factor_bar_interval", "factor_window_bars", "return_bar_interval", "forward_return_bars"):
        if key not in identity or str(identity[key]).casefold() != str(row.get(key)).casefold():
            issues.append(f"{ref}:formula:metric_identity:{key}")
    for public, database in (("lookback", "lookback_json"), ("lag", "lag_json")):
        if database in row and (public not in data or _json(data.get(public)) != _json(row[database])):
            issues.append(f"{ref}:formula:{public}")
    return ReadCheck(1, tuple(issues))


def _window(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    match = re.fullmatch(r"\s*(?:n\s*=\s*)?(\d+)(?:[HhDdMmSs])?\s*", str(value or ""), re.I)
    return int(match[1]) if match else None


def _expression_inputs(expression: str) -> tuple[set[str], ast.Expression | None]:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return set(), None
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    call_names = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    bound = {arg.arg for node in ast.walk(tree) if isinstance(node, ast.Lambda) for arg in node.args.args}
    bound |= {node.target.id for node in ast.walk(tree) if isinstance(node, ast.comprehension) and isinstance(node.target, ast.Name)}
    return names - call_names - bound - _CONTROL, tree


def _wrapper_contract(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        tree = ast.parse(value)
    except SyntaxError:
        return {}
    result: dict[str, Any] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {"required_fields", "derived_fields", "factor_bar_interval", "temporal_unit"}:
                    try:
                        result[target.id] = ast.literal_eval(node.value)
                    except (ValueError, TypeError, SyntaxError):
                        pass
    return result


def _metadata_fields(value: Any) -> set[str]:
    metadata = _mapping(value)
    result = set().union(*(_fields(metadata.get(key)) for key in
                          ("fields", "required_fields", "resolved_raw_fields", "derived_fields")))
    for row in metadata.get("field_resolution", []) or []:
        if isinstance(row, Mapping):
            field = row.get("canonical_field_name") or row.get("field_name")
            if field:
                result.add(str(field))
    return result


def _latest_details(snapshot: FormulaCatalogSnapshot) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in sorted(snapshot.details, key=lambda item: (str(item.get("updated_at") or ""), int(item["id"]))):
        result[_ref(row)] = row
    return result


def formula_candidates(snapshot: FormulaCatalogSnapshot) -> tuple[dict[str, Any], ...]:
    """Discover semantic candidates from actual catalog expressions and wrappers.

    Returns deterministic, credential-free candidate records for every original
    scanner branch. Candidates are not defects without a corresponding semantic
    contract; callers must evaluate or explicitly report that missing contract.
    Syntax is parsed, never executed. Malformed input rows raise KeyError.
    """
    candidates: list[dict[str, Any]] = []
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    definitions = {int(row["id"]): row for row in snapshot.definitions}
    for row in snapshot.details:
        ref, expression = _ref(row), str(row.get("calc_logic") or "")
        params, metadata, wrapper = _mapping(row.get("params")), _mapping(row.get("data_source_metadata")), _wrapper_contract(row.get("calc_function"))
        names, tree = _expression_inputs(expression)
        base = {"factor_ref": ref, "detail_id": int(row["id"])}
        def add(category: str, **evidence: Any) -> None:
            candidates.append({**base, "category": category, **evidence})
        if not expression.strip():
            add("empty_formula")
        elif tree is None:
            add("syntax")
        approved = _fields(params.get("declared_fields")) | _fields(params.get("fields")) | _fields(wrapper.get("required_fields")) | _fields(wrapper.get("derived_fields")) | _metadata_fields(metadata) | _DERIVED
        missing = names - approved
        if missing:
            add("unresolved_inputs", fields=sorted(missing))
        if names - _fields(params.get("fields")) and not missing:
            add("params_only_input_miss", fields=sorted(names - _fields(params.get("fields"))))
        target_names = sorted(name for name in names if name in {"forward_return", "forward_returns", "future_return", "target", "label", "y"} or "return" in name and ("future" in name or "forward" in name))
        if target_names or re.search(r"\b(?:lead|future|forward|anticipat)\w*", str(row.get("name") or ""), re.I):
            add("future_target", fields=target_names)
        if re.search(r"\b(?:8760|365\s*\*\s*24|24\s*\*\s*365|252|365)\b", expression):
            add("annualization")
        interval = params.get("factor_bar_interval") or wrapper.get("factor_bar_interval")
        other = metadata.get("factor_interval") or metadata.get("factor_bar_interval")
        if interval and other and str(interval).lower() != str(other).lower():
            add("interval_mismatch", source="params_vs_metadata")
        for dataset in metadata.get("datasets", []) or []:
            if isinstance(dataset, Mapping):
                target = dataset.get("target_interval") or dataset.get("source_interval")
                if interval and target and str(interval).lower() != str(target).lower():
                    add("dataset_frequency", source="params_vs_dataset")
        for resolution in metadata.get("field_resolution", []) or []:
            if isinstance(resolution, Mapping) and resolution.get("frequency") and interval:
                if str(resolution["frequency"]).lower() not in {str(interval).lower(), "factor_bar_interval"}:
                    add("field_frequency")
        temporal = False
        for call in ast.walk(tree) if tree is not None else ():
            if not isinstance(call, ast.Call):
                continue
            operation = call.func.attr.lower() if isinstance(call.func, ast.Attribute) else call.func.id.lower() if isinstance(call.func, ast.Name) else ""
            if operation in {"shift", "diff", "pct_change", "rolling", "ewm", "expanding"}:
                temporal = True
                positions = call.args[:1] if isinstance(call.func, ast.Attribute) else call.args[1:2]
                periods = [*positions, *(kw.value for kw in call.keywords if kw.arg in {"period", "periods", "window"})]
                if operation in {"shift", "diff", "pct_change"} and any(isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub) for n in periods):
                    add("negative_temporal", operation=operation)
            if operation in {"bfill", "backfill", "lead", "future"} or any(kw.arg == "center" and isinstance(kw.value, ast.Constant) and kw.value.value is True for kw in call.keywords):
                add("center_or_backfill", operation=operation)
            if operation in {"mean", "std", "var", "median", "sum", "min", "max", "quantile", "cov", "corr"} and isinstance(call.func, ast.Attribute):
                receiver, chain = call.func.value, set()
                while isinstance(receiver, ast.Call) and isinstance(receiver.func, ast.Attribute):
                    chain.add(receiver.func.attr.lower())
                    receiver = receiver.func.value
                if not chain & {"rolling", "ewm", "expanding", "groupby", "resample"}:
                    add("unbounded_aggregate", operation=operation)
        if temporal:
            add("temporal_constants")
            definition = definitions.get(int(row["factor_id"]), {}) if row.get("is_sub_factor_id") else {}
            family_name = str(definition.get("sub_factor_name") or row.get("name") or "").lower()
            if not any(name in family_name for name in _KNOWN_FAMILIES):
                add("independent_temporal_family")
            parent = str(params.get("original_sub_factor") or params.get("primary_parent") or "")
            stem = re.sub(r"(?:[_-]\d+(?:\.\d+)?(?:h|hr|hours?|d|days?|m|min|mins|s|sec|secs)?)+$", "", family_name, flags=re.I)
            if "window" not in names and not re.search(r"\bwindow\b", expression):
                window = params.get("factor_window_bars") or params.get("window") or definition.get("window")
                groups[(parent or stem, expression)].append({**base, "window": str(window)})
    for (family, _expression), members in groups.items():
        if family and len({member["window"] for member in members}) > 1:
            for member in members:
                candidates.append({**member, "category": "fixed_formula_family", "family": family})
    return tuple(sorted(candidates, key=lambda item: (item["category"], item["factor_ref"], item["detail_id"])))


class Factor4FormulaService:
    """Reconcile definitions and final evidence while preserving unknown semantics."""

    def __init__(self, api: Factor4FormulaAPI, repository: Factor4FormulaRepository) -> None:
        """Accept protocol/data collaborators without I/O; read errors propagate."""
        self.api, self.repository = api, repository
        self._raw: dict[str, Any] | None = None
        self._formula_checks: dict[tuple[Any, ...], ReadCheck] = {}
        self._candidate_cache: tuple[FormulaCatalogSnapshot, tuple[dict[str, Any], ...]] | None = None

    def _candidates(self, snapshot: FormulaCatalogSnapshot) -> tuple[dict[str, Any], ...]:
        if self._candidate_cache is None or self._candidate_cache[0] is not snapshot:
            self._candidate_cache = snapshot, formula_candidates(snapshot)
        return self._candidate_cache[1]

    def approved_fields(self) -> set[str]:
        """Read the approved global raw schema once; absent mappings block explicitly."""
        if self._raw is None:
            self._raw = read_tool_page(self.api.raw_schema()).data
        fields: set[str] = set()
        for key in ("mappings", "field_resolutions", "fields"):
            for row in self._raw.get(key, []) or []:
                if isinstance(row, Mapping):
                    for field in ("field_name", "canonical_field_name", "name"):
                        if row.get(field):
                            fields.add(str(row[field]))
        if not fields:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: global approved raw schema has no field mapping")
        return fields

    def check_active_catalog(
        self, snapshot: FormulaCatalogSnapshot, *, include_internal_semantics: bool = True,
    ) -> ReadCheck:
        """Reconcile every active eligible reference, executable formula, source and inputs.

        Returns all differences rather than stopping at the first route. Raw
        schema absence blocks; legal MCP errors propagate through the shared
        reader. Historical evidence is counted but never called batch execution.
        Set include_internal_semantics=False for MCP/DB projection only, without
        parsing expressions, resolving operator inputs or scanning formula families.
        """
        approved = self.approved_fields()
        refs = sorted({str(row["factor_ref"]) for row in snapshot.routes})
        if not refs:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: no active eligible formula route")
        details, issues, seen = _latest_details(snapshot), [], set()
        definitions = {int(row["id"]): row for row in snapshot.definitions}
        evidence_refs = {_ref(row) for row in snapshot.evidence if row.get("run_status") == "completed"}
        for offset in range(0, len(refs), 50):
            requested = refs[offset:offset + 50]
            page = read_tool_page(self.api.mcp.get_factor_details_batch(requested, detail_level="executable"))
            for item in page.items:
                ref = item.get("factor_ref")
                if ref not in requested or ref in seen:
                    issues.append("active_formula:unexpected_or_duplicate_ref")
                    continue
                seen.add(ref)
                row, data = details.get(str(ref)), item.get("data")
                if not item.get("success") or not isinstance(data, Mapping) or row is None:
                    issues.append(f"{ref}:missing_executable_or_db_detail")
                    continue
                if str(data.get("calc_logic") or "").strip() != str(row.get("calc_logic") or "").strip():
                    issues.append(f"{ref}:calc_logic")
                if data.get("source_detail_id") is not None and data["source_detail_id"] != row["id"]:
                    issues.append(f"{ref}:source_detail_id")
                if include_internal_semantics:
                    definition = definitions.get(int(row["factor_id"]), {}) if row.get("is_sub_factor_id") else {}
                    names, tree = _expression_inputs(str(row.get("calc_logic") or ""))
                    params, wrapper = _mapping(row.get("params")), _wrapper_contract(row.get("calc_function"))
                    resolved = approved | _DERIVED | _fields(params.get("fields")) | _fields(params.get("declared_fields")) | _metadata_fields(definition.get("metadata")) | _metadata_fields(row.get("data_source_metadata")) | _fields(wrapper.get("required_fields")) | _fields(wrapper.get("derived_fields"))
                    if tree is None:
                        issues.append(f"{ref}:unparseable_current_expression")
                    if names - resolved:
                        issues.append(f"{ref}:unresolved_expression_inputs")
                if ref not in evidence_refs:
                    issues.append(f"{ref}:active_route_without_completed_formula_evidence")
        issues.extend(f"{ref}:missing_batch_detail" for ref in set(refs) - seen)
        candidates = self._candidates(snapshot) if include_internal_semantics else ()
        return ReadCheck(len(refs), tuple(issues), {"independent_candidates": tuple(row for row in candidates if row["category"] == "independent_temporal_family"), "approved_field_count": len(approved)})

    def check_detail_levels(
        self, snapshot: FormulaCatalogSnapshot, factor_id: int, *, include_internal_semantics: bool = True,
    ) -> ReadCheck:
        """Compare summary/definition/executable metadata for one actual sub-factor.

        Missing catalog identity blocks. Formula/fields/source mismatches return
        issues; differing representations are not treated as interchangeable.
        False include_internal_semantics retains projection checks while omitting
        AST equivalence, operator-input resolution and IV/RV mathematics.
        """
        ref = f"sub_factor:{factor_id}"
        detail = _latest_details(snapshot).get(ref)
        definition = next((row for row in snapshot.definitions if row["id"] == factor_id), None)
        if detail is None or definition is None:
            raise ReadPrecondition(f"BLOCKED_DATA_PRECONDITION: no current definition for {ref}")
        approved, issues, levels = self.approved_fields(), [], {}
        for level in ("summary", "definition", "executable"):
            data = read_tool_page(self.api.detail(ref, level)).data
            levels[level] = data
            if data.get("factor_ref") != ref:
                issues.append(f"{ref}:{level}:factor_ref")
            if "window" in data and str(data["window"]).casefold() != str(definition.get("window")).casefold():
                issues.append(f"{ref}:{level}:window")
            if "formula_summary" in data and data["formula_summary"] != definition.get("formula_summary"):
                issues.append(f"{ref}:{level}:formula_summary")
        executable = levels["executable"]
        if executable.get("calc_logic") != detail.get("calc_logic"):
            issues.append(f"{ref}:executable:calc_logic")
        if executable.get("source_detail_id") is not None and executable["source_detail_id"] != detail["id"]:
            issues.append(f"{ref}:executable:source_detail_id")
        params = _mapping(detail.get("params"))
        expected_fields = _metadata_fields(params) | _metadata_fields(detail.get("data_source_metadata"))
        projected_fields = (_metadata_fields(executable.get("params")) | _metadata_fields(executable.get("metadata"))
                            | _metadata_fields(executable.get("data_source_metadata")))
        if expected_fields - projected_fields:
            issues.append(f"{ref}:executable:declared_field_projection")
        if include_internal_semantics:
            names, tree = _expression_inputs(str(detail.get("calc_logic") or ""))
            window = _window(params.get("window") or params.get("factor_window_bars") or definition.get("window"))
            variables = {"window": window} if window else None
            declared_formula = _formula_candidate("definition", definition.get("formula_summary"), variables=variables)
            current_formula = _formula_candidate("detail", detail.get("calc_logic"), variables=variables)
            if declared_formula and current_formula and not _expressions_equivalent(declared_formula[1], current_formula[1], variables=variables):
                issues.append(f"{ref}:definition_executable_formula_conflict")
            resolved = approved | _metadata_fields(detail.get("data_source_metadata")) | _metadata_fields(definition.get("metadata")) | _fields(params.get("fields")) | _DERIVED
            if tree is None or names - resolved:
                issues.append(f"{ref}:executable:unresolved_inputs")
            if factor_id in {161628, 161629, 161630}:
                if not _is_iv_rv_difference(str(detail.get("calc_logic") or "")):
                    issues.append(f"{ref}:F4-IV-RV-DEFINITION-RUNTIME-MISMATCH")
                if not {"atm_iv", "realized_vol"} <= {name.lower() for name in names}:
                    issues.append(f"{ref}:iv_rv:expression_inputs")
                declared = _fields(params.get("fields")) | _metadata_fields(detail.get("data_source_metadata"))
                if not {"atm_iv", "realized_vol"} <= {name.lower() for name in declared}:
                    issues.append(f"{ref}:iv_rv:declared_inputs")
        return ReadCheck(3, tuple(issues))

    def check_exact_formula(self, row: Mapping[str, Any]) -> ReadCheck:
        """Compare one exact completed evidence response including lookback and field resolution.

        Missing server evidence is a contract failure, not empty success. Returns
        differences keyed by DB identity; unknown schema fields are not invented.
        """
        projection_key = json.dumps({key: _json(row.get(key)) for key in (
            "expression", "required_fields", "source_detail_id", "lookback_json", "lag_json",
        )}, sort_keys=True, default=str)
        cache_key = (*_identity(row), row.get("id"), row.get("formula_hash"), row.get("formula_version"), projection_key)
        if cache_key in self._formula_checks:
            return self._formula_checks[cache_key]
        data = read_tool_page(self.api.formula(row)).data
        result = compare_exact_formula_output(row, data)
        self._formula_checks[cache_key] = result
        return result

    def check_evidence_catalog(
        self, snapshot: FormulaCatalogSnapshot, *, include_internal_semantics: bool = True,
    ) -> ReadCheck:
        """Audit every evidence row's input/source/interval and explicit metadata warnings.

        Historical mutable detail changes cannot prove old evidence wrong; those
        comparisons report an actual version precondition. Negative temporal
        expressions are candidates requiring causal-policy verification. Returns
        failures independently of blocked records; no metadata flag alone is PASS.
        False include_internal_semantics checks persisted source/interval metadata
        only, without AST, dependency resolution or negative-offset interpretation.
        """
        if not snapshot.evidence:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: catalog has no formula evidence")
        details = {int(row["id"]): row for row in snapshot.details}
        approved, issues, blocked, warnings = self.approved_fields(), [], [], defaultdict(int)
        for row in snapshot.evidence:
            ref = _ref(row)
            source = details.get(row.get("source_detail_id"))
            names, tree = _expression_inputs(str(row.get("expression") or "")) if include_internal_semantics else (set(), None)
            if include_internal_semantics and tree is None:
                issues.append(f"{ref}:evidence={row['id']}:expression_parse")
            if source is not None:
                if _ref(source) != ref:
                    issues.append(f"{ref}:evidence={row['id']}:source_factor_identity")
                wrapper = _wrapper_contract(source.get("calc_function"))
                resolved = approved | _fields(row.get("required_fields")) | _metadata_fields(source.get("data_source_metadata")) | _fields(wrapper.get("required_fields")) | _fields(wrapper.get("derived_fields")) | _DERIVED
                if include_internal_semantics and names - resolved:
                    issues.append(f"{ref}:evidence={row['id']}:unresolved_inputs")
                params = _mapping(source.get("params"))
                interval = params.get("factor_bar_interval") or wrapper.get("factor_bar_interval")
                if interval and str(interval).casefold() != str(row.get("factor_bar_interval")).casefold():
                    if str(source.get("updated_at") or "") <= str(row.get("recorded_at") or ""):
                        issues.append(f"{ref}:evidence={row['id']}:source_interval")
                    else:
                        blocked.append({"evidence_id": row["id"], "reason": "SOURCE_CHANGED_AFTER_EVIDENCE"})
            else:
                blocked.append({"evidence_id": row["id"], "reason": "SOURCE_DETAIL_MISSING"})
            if not row.get("metadata_complete"):
                blocked.append({"evidence_id": row["id"], "reason": "FORMULA_METADATA_INCOMPLETE"})
            if include_internal_semantics and re.search(r"\.(?:shift|diff|pct_change)\s*\(\s*(?:periods\s*=\s*)?-", str(row.get("expression") or "")):
                blocked.append({"evidence_id": row["id"], "reason": "NEGATIVE_OFFSET_CAUSAL_POLICY_REQUIRED"})
            for warning in _fields(row.get("metadata_warnings")):
                warnings[warning] += 1
        return ReadCheck(len(snapshot.evidence), tuple(issues), {"blocked": blocked, "warning_counts": dict(warnings)})

    def check_persisted_value_run(self, factor_id: int) -> ReadCheck:
        """Verify latest final factor value is finite and linked to same completed formula Run.

        Empty persisted values are data-blocked. A value lacking exact evidence,
        changed interval/window, or a nonfinite value fails without requiring raw
        prices. Returned evidence does not claim independent numerical recompute.
        """
        snapshot = self.repository.factor_results(factor_id)
        if not snapshot.values:
            raise ReadPrecondition(f"BLOCKED_DATA_PRECONDITION: no persisted factor value for sub_factor:{factor_id}")
        value, issues = snapshot.values[0], []
        if value.get("factor_value") is None and value.get("adjusted_factor_value") is None:
            issues.append(f"sub_factor:{factor_id}:latest_value_has_no_numeric_output")
        candidates = [row for row in snapshot.evidence if row["run_id"] == value["run_id"]
                      and row["factor_bar_interval"] == value["factor_bar_interval"]
                      and row["factor_window_bars"] == value["factor_window_bars"]]
        if not candidates:
            issues.append(f"sub_factor:{factor_id}:latest_value_without_exact_completed_formula")
        for field in ("factor_value", "adjusted_factor_value"):
            if value.get(field) is not None:
                try:
                    if not Decimal(str(value[field])).is_finite():
                        issues.append(f"sub_factor:{factor_id}:nonfinite_{field}")
                except (ValueError, ArithmeticError):
                    issues.append(f"sub_factor:{factor_id}:invalid_{field}")
        for row in candidates:
            issues.extend(self.check_exact_formula(row).issues)
        return ReadCheck(1, tuple(issues), {"value_id": value["id"], "formula_evidence_ids": [row["id"] for row in candidates]})

    def check_exact_final_chain(self, factor_id: int, *, reject_other_window: bool = False) -> ReadCheck:
        """Verify latest completed formula, available TS/CS scopes and validity against DB.

        This replaces hard-coded historical Runs for aggregate/5921 probes. Each
        present scope is compared including its actual symbol; an absent scope
        is not a Run failure. For reject_other_window, a syntactically valid
        window proven absent in this exact Run must return FORMULA_EVIDENCE_NOT_FOUND.
        """
        snapshot = self.repository.factor_results(factor_id)
        if not snapshot.evidence:
            raise ReadPrecondition(f"BLOCKED_DATA_PRECONDITION: no completed formula for sub_factor:{factor_id}")
        row = snapshot.evidence[0]
        issues = list(self.check_exact_formula(row).issues)
        summary_api = Factor4SummaryAPI(self.api.mcp)
        summaries = [item for item in snapshot.summaries if _identity(item) == _identity(row)]
        if not summaries:
            return ReadCheck(1, tuple(issues), {"blocked": ["EXACT_FORMULA_RUN_HAS_NO_FINAL_SUMMARY"]})
        scopes: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for item in summaries:
            scopes[tuple(item.get(key) for key in SUMMARY_KEYS)].append(item)
        for expected in scopes.values():
            sample = expected[0]
            data = read_tool_page(summary_api.metrics(_ref(row), summary_scope(sample),
                                   as_of=datetime.now(timezone.utc).isoformat(), run_id=row["run_id"])).data
            actual = data.get("ic_summaries")
            if not isinstance(actual, list):
                raise ReadContractError("exact formula metric response lacks summaries")
            issues.extend(compare_summary(actual, expected).issues)
        validity_rows = [item for item in snapshot.validity if _identity(item) == _identity(row)]
        for validity in validity_rows:
            args = {key: validity[key] for key in SUMMARY_KEYS if key in validity and key not in {"ic_scope", "factor_bar_interval"}}
            args.update(factor_ref=_ref(row), interval=row["factor_bar_interval"], validity_scope="time_series", run_id=row["run_id"], as_of=datetime.now(timezone.utc).isoformat())
            data = read_tool_page(summary_api.validity(args)).data
            for key in ("run_id", "factor_window_bars", "time_series_summary_id", "cross_sectional_summary_id", "time_series_status", "time_series_is_valid", "cross_sectional_status", "cross_sectional_is_valid"):
                if key in validity and data.get(key) != validity[key]:
                    issues.append(f"{_ref(row)}:validity:{key}")
        if reject_other_window:
            used = {str(item["factor_window_bars"]).upper() for item in snapshot.evidence if item["run_id"] == row["run_id"]}
            missing = next(f"{number}H" for number in range(1, 10001) if f"{number}H" not in used)
            body = read_tool_body(self.api.formula(row, window=missing))
            if _mapping(body.get("error")).get("code") != "FORMULA_EVIDENCE_NOT_FOUND":
                issues.append(f"{_ref(row)}:missing_window_not_rejected")
        return ReadCheck(1 + len(scopes) + len(validity_rows) + int(reject_other_window), tuple(issues),
                         {"summary_scope_count": len(scopes), "validity_count": len(validity_rows),
                          "blocked": ["EXACT_RUN_VALIDITY_SAMPLE_MISSING"] if reject_other_window and not validity_rows else []})

    def check_semantic_category(self, snapshot: FormulaCatalogSnapshot, category: str) -> ReadCheck:
        """Evaluate discovered candidates with available metadata and exact formula evidence.

        All scanner branches are data-driven. Unknown intended frequency,
        annualization, derived helper or aggregate semantics are returned as
        blocked records, never as inferred defects. Empty candidate lists prove
        only absence in the inspected catalog. Bad identity/field projections
        remain failures even if a candidate also lacks a semantic contract.
        """
        if category not in _CANDIDATE_CATEGORIES:
            raise ValueError("unsupported formula semantic category")
        all_candidates = self._candidates(snapshot)
        selected = [item for item in all_candidates if item["category"] == category]
        if not snapshot.details:
            raise ReadPrecondition("BLOCKED_DATA_PRECONDITION: formula catalog is empty")
        approved, issues, blocked = self.approved_fields(), [], []
        latest = _latest_details(snapshot)
        active_refs = {str(row["factor_ref"]) for row in snapshot.routes}
        details = {int(row["id"]): row for row in snapshot.details}
        evidence_by_ref: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in snapshot.evidence:
            if row.get("run_status") == "completed":
                evidence_by_ref[_ref(row)].append(row)
        checked_formula: set[int] = set()
        for candidate in selected:
            ref, detail = candidate["factor_ref"], details[candidate["detail_id"]]
            historical = latest.get(ref, {}).get("id") != detail["id"]
            if historical:
                blocked.append({"factor_ref": ref, "detail_id": detail["id"], "reason": "HISTORICAL_DETAIL_NOT_CURRENT"})
                continue
            if category == "unresolved_inputs":
                unresolved = set(candidate["fields"]) - approved
                if unresolved:
                    if ref in active_refs:
                        issues.append(f"{ref}:global_schema_unresolved_inputs")
                    else:
                        blocked.append({"factor_ref": ref, "detail_id": detail["id"], "reason": "UNPUBLISHED_INPUT_RESOLUTION_CONTRACT_REQUIRED"})
            elif category in {"empty_formula", "syntax"}:
                if ref in active_refs and detail.get("is_sub_factor_id"):
                    issues.append(f"{ref}:current_{category}")
                else:
                    blocked.append({"factor_ref": ref, "detail_id": detail["id"], "reason": "CATALOG_EXECUTABLE_DIALECT_CONTRACT_REQUIRED"})
            elif category == "interval_mismatch":
                issues.append(f"{ref}:current_{category}")
            elif category not in {"params_only_input_miss", "temporal_constants"}:
                blocked.append({"factor_ref": ref, "detail_id": detail["id"], "reason": f"SEMANTIC_CONTRACT_REQUIRED:{category}"})
            if category not in {"fixed_formula_family", "unbounded_aggregate"}:
                continue
            formulas = evidence_by_ref.get(ref, [])
            if formulas:
                formula = max(formulas, key=lambda item: (str(item.get("run_completed_at") or ""), int(item["id"])))
                if int(formula["id"]) not in checked_formula:
                    checked_formula.add(int(formula["id"]))
                    issues.extend(self.check_exact_formula(formula).issues)
            elif category in {"fixed_formula_family", "unbounded_aggregate", "independent_temporal_family"}:
                blocked.append({"factor_ref": ref, "detail_id": detail["id"], "reason": "NO_COMPLETED_FORMULA_EVIDENCE"})
        return ReadCheck(len(snapshot.details), tuple(issues), {"candidate_count": len(selected), "blocked": blocked,
                                                             "exact_formula_count": len(checked_formula)})

    def check_candidate_horizon(self, snapshot: FormulaCatalogSnapshot, factor_id: int) -> ReadCheck:
        """Read current/detail/exact evidence and conditionally evaluate temporal dependencies.

        An evaluation window does not define a feature horizon by itself.
        Only the documented funding-diff2 and long-short-change families have
        a maximum-offset contract; all other candidates keep parsed offsets and
        an explicit contract requirement after their MCP/DB identity is checked.
        """
        ref = f"sub_factor:{factor_id}"
        detail = _latest_details(snapshot).get(ref)
        if detail is None:
            raise ReadPrecondition(f"BLOCKED_DATA_PRECONDITION: no formula candidate {ref}")
        issues = list(self.check_detail_levels(snapshot, factor_id).issues)
        params = _mapping(detail.get("params"))
        declared = _window(params.get("factor_window_bars") or params.get("window"))
        expressions = [("current", str(detail.get("calc_logic") or ""))]
        evidence = [row for row in snapshot.evidence if _ref(row) == ref and row.get("run_status") == "completed"]
        blocked, offsets = [], {}
        if evidence:
            formula = max(evidence, key=lambda item: (str(item.get("run_completed_at") or ""), int(item["id"])))
            issues.extend(self.check_exact_formula(formula).issues)
            expressions.append(("latest_completed", str(formula.get("expression") or "")))
        else:
            blocked.append("NO_COMPLETED_FORMULA_EVIDENCE")
        for source, expression in expressions:
            try:
                offsets[source] = formula_dependency_offsets(expression, variables={"window": declared} if declared else {})
            except FormulaOffsetError:
                blocked.append(f"OFFSET_ORACLE_UNSUPPORTED:{source}")
        known_fixed = {180: ("funding_diff2", 12, 24), 181: ("funding_diff2", 24, 48),
                       183: ("funding_diff2", 36, 72), 274: ("long_short_pct", 48, 48),
                       276: ("long_short_pct", 72, 72)}
        if factor_id in known_fixed and declared is not None:
            if offsets.get("current") and max(offsets["current"]) != declared:
                issues.append(f"{ref}:declared_dependency_horizon")
            family, period, required_window = known_fixed[factor_id]
            if declared != required_window or not _is_correct_fixed_horizon(str(detail.get("calc_logic") or ""), family, period, declared):
                issues.append(f"{ref}:F4-FIXED-HORIZON-FORMULA")
        elif factor_id in {161104, 161106, 161108} and declared is not None:
            if not _is_correct_dpo(str(detail.get("calc_logic") or ""), declared):
                issues.append(f"{ref}:F4-DPO-FORMULA")
        else:
            blocked.append("FEATURE_HORIZON_CONTRACT_REQUIRED")
        return ReadCheck(len(expressions), tuple(issues), {"dependency_offsets": offsets, "blocked": blocked,
                                                          "declared_window": declared})

    def check_candidate_projection(self, snapshot: FormulaCatalogSnapshot, factor_id: int) -> ReadCheck:
        """Retain current/detail/completed-evidence output checks from a horizon Case.

        The explicit factor ID is selected from the captured catalog; an absent
        current definition raises ReadPrecondition. Returns MCP projection issues
        and a missing-completed-evidence precondition, without deriving offsets or
        judging DPO/fixed-window semantics. API/DB exceptions propagate unchanged.
        """
        ref = f"sub_factor:{factor_id}"
        detail = self.check_detail_levels(snapshot, factor_id, include_internal_semantics=False)
        issues = list(detail.issues)
        evidence = [row for row in snapshot.evidence if _ref(row) == ref and row.get("run_status") == "completed"]
        if not evidence:
            return ReadCheck(detail.checked_count, tuple(issues), {"blocked": ["NO_COMPLETED_FORMULA_EVIDENCE"]})
        formula = max(evidence, key=lambda item: (str(item.get("run_completed_at") or ""), int(item["id"])))
        exact = self.check_exact_formula(formula)
        return ReadCheck(detail.checked_count + exact.checked_count, (*issues, *exact.issues))
