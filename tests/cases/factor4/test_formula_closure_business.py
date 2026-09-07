"""Real final route/formula/schema references, without raw-value or fixture-file shortcuts."""

import pytest

from db.factor4_calculation_repository import CalculationAuditSnapshot, Factor4CalculationRepository
from db.factor4_schema_repository import ApprovedSchemaSnapshot, Factor4SchemaRepository
from service.factor4_formula_closure_service import Factor4FormulaClosureService, metric_raw_schema_version
from service.factor4_result_service import ENVIRONMENT_LABELS

pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.factor4_calculation]


@pytest.fixture(scope="module")
def bound_raw_schemas(
    factor4_closure_snapshots: tuple[CalculationAuditSnapshot, ...],
    factor4_calculation_repository: Factor4CalculationRepository,
) -> dict[str, ApprovedSchemaSnapshot | None]:
    """Read only explicitly referenced approved versions; leave malformed links to assertions.

    No schema/current fallback or writes occur. Database failures propagate.
    """
    versions = set()
    for snapshot in factor4_closure_snapshots:
        for metric in snapshot.evaluation_metrics:
            try:
                version = metric_raw_schema_version(metric)
            except ValueError:
                continue
            if version:
                versions.add(version)
    repository = Factor4SchemaRepository(factor4_calculation_repository.database_client)
    return {version: repository.approved(version) for version in sorted(versions)}


@pytest.mark.parametrize("label", ENVIRONMENT_LABELS)
@pytest.mark.parametrize("include_schema", [False, True], ids=["immutable-formula", "versioned-input-closure"])
def test_published_routes_bind_exact_formula_and_schema(
    factor4_closure_snapshots: tuple[CalculationAuditSnapshot, ...],
    request: pytest.FixtureRequest, label: str, include_schema: bool,
) -> None:
    """Compare final references per label; contradictions fail before any missing evidence skips."""
    service = Factor4FormulaClosureService()
    schemas = request.getfixturevalue("bound_raw_schemas") if include_schema else None
    results = [service.check_routes(snapshot, label, schemas=schemas)
               for snapshot in factor4_closure_snapshots]
    assert not [issue for result in results for issue in result.issues]
    blocked = sorted({reason for result in results for reason in result.evidence["blocked"]})
    if blocked:
        pytest.skip(f"BLOCKED_DATA_PRECONDITION: formula chain incomplete: {blocked[:15]}")
    assert sum(result.checked_count for result in results) > 0
