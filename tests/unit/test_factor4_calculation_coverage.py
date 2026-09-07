"""Factor 4.0 pending-calculation coverage manifest tests."""

from __future__ import annotations

import pytest

from service.factor4_calculation_coverage import (
    Factor4CalculationCoverageService,
    Factor4CoverageCase,
    PENDING_FACTOR4_CALCULATION_CASES,
)


EXPECTED_CASE_IDS = (
    "ENV-104-A",
    "LIFE-405-A",
    "CALC-501-A",
    "CALC-501-B",
    "CALC-502-A",
    "CALC-502-B",
    "CALC-503-A",
    "CALC-503-B",
    "CALC-505-A",
    "CALC-507-B",
    "CALC-508-A",
    "CALC-508-B",
    "CALC-510-B",
    "CALC-511-A",
    "CALC-511-B",
    "CALC-512-A",
    "CALC-512-B",
    "CALC-509",
)


def test_default_manifest_covers_all_documented_pending_cases() -> None:
    """All data/fixture-dependent sub-cases remain visible and ordered."""

    service = Factor4CalculationCoverageService()

    assert tuple(case.case_id for case in service.cases) == EXPECTED_CASE_IDS
    assert tuple(case.case_id for case in PENDING_FACTOR4_CALCULATION_CASES) == EXPECTED_CASE_IDS
    assert len({case.case_id for case in service.cases}) == 18
    assert all(case.required_preconditions for case in service.cases)


def test_manifest_is_serializable_and_contains_no_runtime_secret() -> None:
    """Manifest entries contain only executor metadata and redacted status rules."""

    manifest = Factor4CalculationCoverageService().manifest()

    assert len(manifest) == 18
    required_keys = {
        "case_id",
        "module",
        "title",
        "mode",
        "oracle",
        "preconditions",
        "on_missing_precondition",
    }
    for entry in manifest:
        assert set(entry) == required_keys
        assert entry["on_missing_precondition"] == "BLOCKED_DATA_PRECONDITION"
        assert isinstance(entry["preconditions"], list)
        rendered = repr(entry)
        assert "Authorization" not in rendered
        assert "Bearer" not in rendered
        assert "naf_mcp_" not in rendered


def test_missing_preconditions_are_reported_per_case() -> None:
    """An empty fixture map blocks every case with its exact missing names."""

    service = Factor4CalculationCoverageService()

    assessments = service.assess({})
    checks = service.build_blocked_checks({})

    assert len(assessments) == len(checks) == 18
    for assessment, check in zip(assessments, checks, strict=True):
        assert assessment.status == "BLOCKED_DATA_PRECONDITION"
        assert assessment.missing_preconditions == assessment.case.required_preconditions
        assert check.case_id == assessment.case.case_id
        assert check.status == "BLOCKED_DATA_PRECONDITION"
        assert check.checked_count == 0
        assert check.findings[0].code == f"{check.case_id}_PRECONDITION_MISSING"
        assert check.findings[0].evidence["missing_preconditions"] == list(
            assessment.missing_preconditions
        )
        assert check.findings[0].evidence["missing_reason_codes"] == list(
            assessment.missing_reason_codes
        )


def test_non_boolean_availability_fails_closed() -> None:
    """Only an exact True value satisfies a fixture precondition."""

    service = Factor4CalculationCoverageService()
    first = service.cases[0]

    assessment = service.assess({first.required_preconditions[0]: 1})[0]

    assert assessment.status == "BLOCKED_DATA_PRECONDITION"
    assert assessment.missing_preconditions == first.required_preconditions


def test_unknown_precondition_is_rejected_instead_of_ignored() -> None:
    """A misspelled fixture key must not create a false READY result."""

    service = Factor4CalculationCoverageService()

    with pytest.raises(ValueError, match="unknown Factor 4.0 preconditions"):
        service.assess({"RAW_FORWARD_RETURNS_MISSPELLED": True})


def test_non_string_precondition_key_is_rejected() -> None:
    """Malformed provider output must fail closed with a useful error."""

    service = Factor4CalculationCoverageService()

    with pytest.raises(ValueError, match="precondition keys must be strings"):
        service.assess({1: True})  # type: ignore[dict-item]


def test_partial_availability_preserves_only_missing_names() -> None:
    """The gate exposes the exact remaining data gap for a case."""

    service = Factor4CalculationCoverageService()
    case = next(item for item in service.cases if item.case_id == "CALC-508-B")
    availability = {name: True for name in case.required_preconditions[:2]}

    assessment = next(item for item in service.assess(availability) if item.case.case_id == case.case_id)

    assert assessment.status == "BLOCKED_DATA_PRECONDITION"
    assert assessment.missing_preconditions == case.required_preconditions[2:]
    assert assessment.missing_reason_codes == tuple(
        f"{name}_MISSING" for name in case.required_preconditions[2:]
    )


def test_all_preconditions_ready_does_not_become_product_pass() -> None:
    """Fixture readiness alone remains blocked until an independent live oracle runs."""

    service = Factor4CalculationCoverageService()
    availability = {
        name: True
        for case in service.cases
        for name in case.required_preconditions
    }

    assessments = service.assess(availability)
    checks = service.build_blocked_checks(availability)

    assert all(item.ready for item in assessments)
    assert all(item.status == "BLOCKED_DATA_PRECONDITION" for item in checks)
    assert all(item.findings[0].code == "LIVE_ORACLE_EXECUTION_REQUIRED" for item in checks)


@pytest.mark.parametrize(
    "bad_case",
    [
        Factor4CoverageCase(
            " ",
            "factor4.test",
            "bad",
            "READ_ONLY",
            "oracle",
            ("PRECONDITION",),
        ),
        Factor4CoverageCase(
            "DUP",
            "factor4.test",
            "bad",
            "READ_ONLY",
            "oracle",
            ("PRECONDITION", "PRECONDITION"),
        ),
    ],
    ids=["blank-id", "duplicate-precondition"],
)
def test_custom_manifest_is_validated_before_use(bad_case: Factor4CoverageCase) -> None:
    """Malformed extension manifests fail before they reach an executor."""

    with pytest.raises(ValueError):
        Factor4CalculationCoverageService((bad_case,))


def test_duplicate_case_ids_are_rejected() -> None:
    """Two entries with one case identity would make reports ambiguous."""

    case = PENDING_FACTOR4_CALCULATION_CASES[0]

    with pytest.raises(ValueError, match="duplicate Factor 4.0 coverage case_id"):
        Factor4CalculationCoverageService((case, case))
