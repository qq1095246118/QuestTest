"""Regression tests for isolated formula-family business checks."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.unit.test_factor4_calculation_service import (
    TestKnownFormulaRegressions as _KnownFormulaFixture,
    _service,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(("family", "count"), [("dpo", 3), ("fixed_horizon", 5), ("iv_rv", 3)])
def test_isolated_formula_family_checks_only_its_members(family: str, count: int) -> None:
    """Each family must have its own nonempty evidence and deterministic count."""
    snapshot, api = _KnownFormulaFixture._complete_fixture()
    service, _ = _service(snapshot, mcp=api)
    result = service.check_known_formula_regressions(snapshot, family=family)
    assert result.status == "PASS", result.findings
    assert result.checked_count == count
    assert result.evidence["family"] == family


def test_unrelated_wrong_formula_does_not_fail_selected_family() -> None:
    """A DPO defect cannot contaminate the IV/RV result."""
    snapshot, api = _KnownFormulaFixture._complete_fixture()
    snapshot = replace(snapshot, details=tuple(
        replace(row, calc_logic="close.shift(-20)") if row.factor_ref == "sub_factor:161104" else row
        for row in snapshot.details
    ))
    service, _ = _service(snapshot, mcp=api)
    result = service.check_known_formula_regressions(snapshot, family="iv_rv")
    assert result.status == "PASS", result.findings


def test_unknown_formula_family_is_rejected_before_any_read() -> None:
    """Unknown selectors may not silently run all families."""
    snapshot, api = _KnownFormulaFixture._complete_fixture()
    service, _ = _service(snapshot, mcp=api)
    with pytest.raises(ValueError, match="unsupported known formula"):
        service.check_known_formula_regressions(snapshot, family="unknown")


@pytest.mark.parametrize("field", ["formula_hash", "formula_version", "run_id", "source_detail_id"])
def test_known_semantics_do_not_hide_formula_projection_identity_drift(field: str) -> None:
    """Correct DPO math is insufficient when its exact evidence identity drifts."""
    snapshot, api = _KnownFormulaFixture._complete_fixture()
    target = api.formulas["sub_factor:161104"]
    target[field] = 999999 if field == "source_detail_id" else "different-evidence"
    service, _ = _service(snapshot, mcp=api)
    result = service.check_known_formula_regressions(snapshot, family="dpo")
    assert result.status == "FAIL", result.findings
    assert any(finding.factor_ref == "sub_factor:161104" for finding in result.findings)
