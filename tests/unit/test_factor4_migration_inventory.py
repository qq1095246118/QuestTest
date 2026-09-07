"""迁移清单结构自检；不是 Factor 4.0 业务测试，不计业务覆盖。"""

import ast
from pathlib import Path

import pytest

from service.factor4_case_registry import factor4_script_migrations

pytestmark = pytest.mark.unit
_ROOT = Path(__file__).resolve().parents[2]


def test_all_61_original_sources_are_adjudicated_and_all_56_test_scripts_are_retired() -> None:
    migrations = factor4_script_migrations()
    assert len(migrations) == 61
    assert sum(item.status == "ASSERTED" for item in migrations) == 56
    assert sum(item.status == "NON_CASE_TOOL" for item in migrations) == 5
    assert all(item.source_removed for item in migrations if item.status == "ASSERTED")


def test_reviewed_migrations_reference_concrete_business_functions() -> None:
    for migration in factor4_script_migrations():
        if migration.status not in {"ASSERTED", "PARTIAL"}:
            continue
        assert migration.test_nodes
        for reference in migration.test_nodes:
            module, name = reference.split("::", 1) if "::" in reference else (migration.case_module, reference)
            target = _ROOT / module
            assert target.is_file()
            tree = ast.parse(target.read_text(encoding="utf-8"))
            functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
            assert name in functions, (migration.script_name, name)
            body = ast.unparse(functions[name])
            assert "tmp/" not in body and "subprocess" not in body
            assert "check_" in body or "assert " in body or "_require_check" in body


def test_removed_sources_are_asserted_and_remaining_sources_are_preserved() -> None:
    migrations = factor4_script_migrations()
    sources = {path.name for path in (_ROOT / "tmp").glob("*.py")}
    assert sources == {item.script_name for item in migrations if not item.source_removed}
    for item in migrations:
        if item.source_removed:
            assert item.status == "ASSERTED" and item.test_nodes
        if item.status == "REMAINING":
            assert not item.case_module and not item.test_nodes and not item.source_removed


def test_business_suite_has_no_inventory_only_case() -> None:
    assert not (_ROOT / "tests/cases/factor4/test_migrated_script_inventory.py").exists()
    assert not (_ROOT / "tests/cases/factor4/test_calculation_coverage.py").exists()


def test_migrated_layers_do_not_import_temporary_runners_or_reverse_dependencies() -> None:
    for directory in ("api", "db", "service", "tests/cases/factor4"):
        pattern = "*.py" if directory.startswith("tests/") else "*factor4*.py"
        for path in (_ROOT / directory).glob(pattern):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)
            assert not any(name.split(".")[0] in {"tmp", "subprocess"} for name in imports), path
            if directory in {"api", "db"}:
                assert not any(name.split(".")[0] in {"service", "tests"} for name in imports), path
            if directory == "service":
                assert not any(name.split(".")[0] in {"tests", "pytest"} for name in imports), path


def test_formula_migration_case_never_skips_a_known_static_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from tests.cases.factor4 import test_formula_route_audit_migrations as cases

    blocked = SimpleNamespace(status="BLOCKED_DATA_PRECONDITION", case_id="integrity", summary="missing evidence")
    failure = SimpleNamespace(status="FAIL", findings=[SimpleNamespace(code="REAL_STATIC_MISMATCH", factor_ref="sub_factor:1")])
    service = SimpleNamespace(check_formula_integrity=lambda _: blocked,
                              check_formula_static_consistency=lambda _: failure)
    monkeypatch.setattr(cases, "_calc_service", lambda *_: service)
    with pytest.raises(AssertionError, match="REAL_STATIC_MISMATCH"):
        cases.test_formula_integrity_and_source_chain(None, None, None)
