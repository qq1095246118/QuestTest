"""Formal approved-schema business assertions migrated from cross-module scripts."""

from collections.abc import Callable

import pytest

from api.factor4_schema_api import Factor4SchemaAPI
from api.factor_data_mcp_api import FactorDataMCPAPI
from db.factor4_calculation_repository import Factor4CalculationRepository
from db.factor4_schema_repository import ApprovedSchemaSnapshot, Factor4SchemaRepository
from service.factor4_read_service import ReadCheck, ReadPrecondition
from service.factor4_schema_service import Factor4SchemaService

pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.factor4_calculation]


@pytest.fixture(scope="module")
def approved_schema(factor4_calculation_repository: Factor4CalculationRepository) -> ApprovedSchemaSnapshot:
    """Read actual approved version; missing data blocks and DB failures remain errors."""
    snapshot = Factor4SchemaRepository(factor4_calculation_repository.database_client).approved()
    if snapshot is None:
        pytest.skip("BLOCKED_DATA_PRECONDITION: no approved raw-data schema")
    return snapshot


@pytest.fixture(scope="module")
def schema_service(factor_data_mcp_api: FactorDataMCPAPI) -> Factor4SchemaService:
    """Reuse explicit live/test-gated transport, initializing its read-only session."""
    if factor_data_mcp_api.protocol_version is None:
        factor_data_mcp_api.initialize()
        factor_data_mcp_api.notify_initialized()
    return Factor4SchemaService(Factor4SchemaAPI(factor_data_mcp_api))


def _check(action: Callable[[], ReadCheck]) -> None:
    try:
        result = action()
    except ReadPrecondition as exc:
        pytest.skip(str(exc))
    assert result.checked_count > 0
    assert not result.issues, result.issues[:30]


@pytest.mark.parametrize("raw", [False, True], ids=["factor_fields", "raw_schema"])
@pytest.mark.parametrize("explicit", [False, True], ids=["default", "explicit"])
def test_approved_schema_complete_members_and_fields_match_database(schema_service: Factor4SchemaService, approved_schema: ApprovedSchemaSnapshot, raw: bool, explicit: bool) -> None:
    """SCH-001/004: default and explicit version reconcile full DB mappings/resolutions/replays."""
    _check(lambda: schema_service.check_approved(approved_schema, raw=raw, explicit=explicit))


@pytest.mark.parametrize("selector", ["vwap", "close", "discovered"])
def test_selected_schema_fields_dependencies_and_replay_are_exact(schema_service: Factor4SchemaService, approved_schema: ApprovedSchemaSnapshot, selector: str) -> None:
    """SCH-002/STABLE: known single-field selector never leaks unrelated fields or dependencies."""
    _check(lambda: schema_service.check_selected(approved_schema, selector))


@pytest.mark.factor4_deferred
@pytest.mark.parametrize("variant", ["field", "fields_version", "raw_version", "extra"])
def test_schema_invalid_selectors_never_fall_back_to_approved_data(schema_service: Factor4SchemaService, variant: str) -> None:
    """SCH-003/005/006/007: keep excluded abnormal-selector scenarios executable separately."""
    _check(lambda: schema_service.check_invalid_selector(variant))
