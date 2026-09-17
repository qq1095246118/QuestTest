"""Catalog transfer preserves historical rows and complete consumed payloads."""

from datetime import datetime
import json
import zlib

import pytest

from db.factor4_formula_repository import Factor4FormulaRepository
from service.factor4_formula_service import formula_candidates
from tests.unit.test_factor4_formula_catalog import _catalog, _detail
from tests.unit.test_factor4_summary_repository import _DB


pytestmark = pytest.mark.unit


def _compressed(value: str) -> bytes:
    payload = value.encode("utf-8")
    return len(payload).to_bytes(4, "little") + zlib.compress(payload)


def test_catalog_restores_wrapper_and_entire_metadata_before_business_checks() -> None:
    wrapper = "required_fields = ['close', 'volume']\nfactor_bar_interval = '1h'\n# 原样保留\n"
    metadata = json.dumps({
        "fields": ["close"], "derived_fields": None,
        "field_resolution": [{"field_name": "close", "frequency": "1d"}],
        "datasets": [{"source_interval": "8h", "unknown_nested": [None, {"中文": 1}]}],
        "unknown_future_contract": {"text": "keep", "null": None},
    }, ensure_ascii=False, separators=(",", ":"))
    original = _detail("close + volume", calc_function=wrapper, data_source_metadata=metadata)
    transported = {**original, "calc_function": _compressed(wrapper),
                   "data_source_metadata": _compressed(metadata)}
    definition = {"id": 8, "sub_factor_name": "momentum_24h", "metadata": metadata}
    evidence = [{"id": 1, "run_status": None}, {"id": 2, "run_status": "failed"},
                {"id": 3, "run_status": "completed"}]
    db = _DB([{"as_of_time": datetime(2026, 9, 10)}], [
        [transported], [{**definition, "metadata": _compressed(metadata)}], evidence, [],
    ])
    actual = Factor4FormulaRepository(db).catalog()
    assert actual.details == (original,)
    assert actual.definitions == (definition,)
    assert actual.evidence == tuple(evidence)
    assert formula_candidates(actual) == formula_candidates(_catalog(original))
    assert db.calls[-1] == ("ROLLBACK", ())
    queries = [sql for sql, _ in db.calls if "SELECT" in sql]
    evidence_sql = next(sql for sql in queries if "FROM factor_ic_run_formula_evidence" in sql)
    assert "LEFT JOIN factor_ic_runs" in evidence_sql
    assert "ORDER BY e.id" in evidence_sql
    assert "WHERE" not in evidence_sql and "LIMIT" not in evidence_sql and "GROUP BY" not in evidence_sql
    assert "e.*" not in evidence_sql
    assert "e.normalized_ast" not in evidence_sql and "e.evidence_json" not in evidence_sql
    for column in ("lookback_json", "lag_json", "metadata_warnings", "source_detail_id",
                   "required_fields", "formula_hash", "formula_version", "recorded_at"):
        assert f"e.{column}" in evidence_sql
    assert all("JSON_OBJECT" not in sql and "JSON_REMOVE" not in sql for sql in queries)


@pytest.mark.parametrize("payload", [None, "", "null", "{}", {"unexpected": None}])
def test_catalog_uncompressed_driver_values_keep_null_empty_and_object_semantics(payload: object) -> None:
    detail = {"id": 1, "calc_function": payload, "data_source_metadata": payload}
    definition = {"id": 2, "metadata": payload}
    db = _DB([{"as_of_time": datetime(2026, 9, 10)}], [[detail], [definition], [], []])
    result = Factor4FormulaRepository(db).catalog()
    assert result.details == (detail,)
    assert result.definitions == (definition,)


def test_catalog_malformed_compression_rolls_back_without_payload_in_exception() -> None:
    db = _DB([{"as_of_time": datetime(2026, 9, 10)}], [
        [{"calc_function": b"private-malformed-content"}], [], [], [],
    ])
    with pytest.raises(RuntimeError, match="Formula catalog read failed: ValueError") as error:
        Factor4FormulaRepository(db).catalog()
    assert "private-malformed-content" not in str(error.value)
    assert db.calls[-1] == ("ROLLBACK", ())
