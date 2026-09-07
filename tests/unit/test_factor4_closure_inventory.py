"""Guard the remaining-scenario migration against local-fixture false passes."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.cases.factor4 import conftest as closure_fixtures
from tests.cases.factor4 import test_formula_closure_business as formula_cases
from service.factor4_read_service import ReadCheck

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def test_fixture_only_business_placeholder_is_retired() -> None:
    """No product case may treat a user-authored raw JSON file as a live calculation."""
    assert not (ROOT / "tests/cases/factor4/test_calculation_deferred_business.py").exists()
    for path in (ROOT / "tests/cases/factor4").glob("*.py"):
        assert "FACTOR4_RAW_FIXTURE_JSON" not in path.read_text(encoding="utf-8")


def test_new_closure_cases_require_live_database_evidence() -> None:
    """Each new module consumes a gated DB snapshot and contains no local oracle-only Case."""
    for path in (ROOT / "tests/cases/factor4").glob("test_*closure_business.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        tests = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")]
        assert tests, path
        for node in tests:
            names = {arg.arg for arg in node.args.args}
            assert names & {"factor4_closure_snapshots", "lifecycle_snapshot"}, (path, node.name)
            source = ast.unparse(node)
            assert "check_" in source and ("assert " in source or "_verify(" in source), (path, node.name)
            assert "snapshot_hash" not in source, (path, node.name)


def test_formula_case_preserves_failures_when_another_partition_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """One partition lacking data cannot turn another partition's corruption into skip."""
    results = iter((ReadCheck(0, (), {"blocked": ("missing",)}), ReadCheck(1, ("corrupt",), {"blocked": ()})))
    monkeypatch.setattr(formula_cases.Factor4FormulaClosureService, "check_routes", lambda *a, **kw: next(results))
    with pytest.raises(AssertionError):
        request = SimpleNamespace(getfixturevalue=lambda name: {})
        formula_cases.test_published_routes_bind_exact_formula_and_schema((None, None), request, "WIDE_RANGE", True)


def test_snapshot_discovery_rejects_duplicate_active_publication() -> None:
    """Multiple current rows must not silently select a convenient batch."""
    part = SimpleNamespace(id=1, publication_uid="p", publish_version="v", market_scope="all", route_profile_key="default")
    repository = SimpleNamespace(list_active_published_partitions=lambda: (part, part))
    with pytest.raises(AssertionError, match="multiple active"):
        closure_fixtures.factor4_closure_snapshots.__wrapped__(repository)


def test_snapshot_discovery_does_not_certify_changed_publication() -> None:
    """Separate discovery/read transactions explicitly reject a changed publication."""
    part = SimpleNamespace(id=1, publication_uid="p", publish_version="v", market_scope="all", route_profile_key="default")
    snapshot = SimpleNamespace(batch=SimpleNamespace(id=2, publication_uid="p2", publish_version="v2"))
    repository = SimpleNamespace(list_active_published_partitions=lambda: (part,), read_calculation_snapshot=lambda *args, **kwargs: snapshot)
    with pytest.raises(pytest.skip.Exception, match="publication changed"):
        closure_fixtures.factor4_closure_snapshots.__wrapped__(repository)


def test_closure_fixture_requests_history_independent_of_frozen_members() -> None:
    """The business fixture must request full history, not certify its own member list."""
    from unittest.mock import MagicMock

    part = SimpleNamespace(id=1, publication_uid="p", publish_version="v", market_scope="all", route_profile_key="default")
    snapshot = SimpleNamespace(batch=part)
    repository = SimpleNamespace(list_active_published_partitions=lambda: (part,),
                                 read_calculation_snapshot=MagicMock(return_value=snapshot))
    assert closure_fixtures.factor4_closure_snapshots.__wrapped__(repository) == (snapshot,)
    repository.read_calculation_snapshot.assert_called_once_with("all", "default", include_full_environment_history=True)
