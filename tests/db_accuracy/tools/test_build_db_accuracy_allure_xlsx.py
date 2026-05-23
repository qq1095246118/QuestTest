from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path


def _workspace_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "tools" / "db_accuracy").is_dir():
            return parent
    raise RuntimeError("Unable to locate QuestTest workspace root")


def _load_script_module():
    script = (
        _workspace_root()
        / "tools"
        / "db_accuracy"
        / "build_allure_xlsx.py"
    )
    spec = importlib.util.spec_from_file_location("build_db_accuracy_allure_xlsx", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_latest_accuracy_attachment_ignores_non_accuracy_json(tmp_path):
    module = _load_script_module()
    old_valid = tmp_path / "old-attachment.json"
    newest_invalid = tmp_path / "newest-attachment.json"
    newest_valid = tmp_path / "valid-attachment.json"

    old_valid.write_text(json.dumps({"passed": True, "tables": []}), encoding="utf-8")
    newest_invalid.write_text(json.dumps({"name": "not db accuracy"}), encoding="utf-8")
    newest_valid.write_text(
        json.dumps(
            {
                "passed": False,
                "tables": [
                    {
                        "table": "binance_kline_all_future_raw",
                        "passed": False,
                        "windows_checked": 1,
                        "db_rows_checked": 1,
                        "source_rows_checked": 1,
                        "differences": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    old_valid.touch()
    newest_invalid.touch()
    newest_valid.touch()

    assert module.find_latest_accuracy_attachment(tmp_path) == newest_valid


def test_latest_accuracy_attachment_searches_nested_table_directories(tmp_path):
    module = _load_script_module()
    old_root = tmp_path / "old-attachment.json"
    nested = tmp_path / "db_accuracy" / "binance_kline_all_future_raw_1h" / "new-attachment.json"
    nested.parent.mkdir(parents=True)

    old_root.write_text(json.dumps({"passed": True, "tables": []}), encoding="utf-8")
    nested.write_text(
        json.dumps(
            {
                "passed": False,
                "tables": [
                    {
                        "table": "binance_kline_all_future_raw_1h",
                        "passed": False,
                        "windows_checked": 0,
                        "db_rows_checked": 0,
                        "source_rows_checked": 0,
                        "differences": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    old_root.touch()
    nested.touch()

    assert module.find_latest_accuracy_attachment(tmp_path) == nested


def test_direct_payload_xlsx_has_chinese_headers_and_text_values(tmp_path):
    module = _load_script_module()
    payload = {
        "passed": False,
        "tables": [
            {
                "table": "binance_kline_all_future_raw",
                "passed": False,
                "windows_checked": 1,
                "db_rows_checked": 1,
                "source_rows_checked": 1,
                "differences": [
                    {
                        "table": "binance_kline_all_future_raw",
                        "key_label": "interval=1m,symbol=BTCUSDT",
                        "row_key": 1704067200000,
                        "field": "volume",
                        "db_value": "12345678901234567890",
                        "source_value": "12345678901234567891",
                        "reason": "value_mismatch",
                    }
                ],
            }
        ],
    }
    output = tmp_path / "result.xlsx"

    module.write_accuracy_workbook(payload, source_path=tmp_path / "source.json", output_path=output)

    with zipfile.ZipFile(output) as zf:
        sheet_xml = zf.read("xl/worksheets/sheet2.xml").decode("utf-8")

    assert "<row r=\"1\">" in sheet_xml
    assert "<t xml:space=\"preserve\">表名</t>" in sheet_xml
    assert "<t xml:space=\"preserve\">源值</t>" in sheet_xml
    assert "<t xml:space=\"preserve\">异常点说明</t>" in sheet_xml
    assert "<t xml:space=\"preserve\">12345678901234567891</t>" in sheet_xml
    assert "1.2345678901234568E+19" not in sheet_xml
    assert "<t xml:space=\"preserve\">2024-01-01 00:00:00</t>" in sheet_xml


def test_partitioned_payload_xlsx_has_partition_summary(tmp_path):
    module = _load_script_module()
    payload = {
        "run_id": "20260523T092841553300Z",
        "status": "passed",
        "tasks_total": 1,
        "tasks_compared": 1,
        "tasks_with_differences": 0,
        "db_rows": 88,
        "source_rows": 88,
        "differences": 0,
        "failure_reason": None,
        "pause_reason": None,
        "partitions": [
            {
                "table": "binance_usdm_funding_rate_raw",
                "endpoint": "usdm_funding",
                "market_key": {"symbol": "BNBUSDT"},
                "start_ms": 1747929600000,
                "end_ms": 1750463999999,
                "status": "passed",
                "db_rows": 88,
                "source_rows": 88,
                "differences": 0,
                "report_path": "compare/table=binance_usdm_funding_rate_raw/report.txt",
                "diff_path": "compare/table=binance_usdm_funding_rate_raw/diff.json",
                "message": None,
            }
        ],
    }
    source = tmp_path / "partitioned-attachment.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "partitioned.xlsx"

    assert module.find_latest_accuracy_attachment(tmp_path) == source

    module.write_accuracy_workbook(payload, source_path=source, output_path=output)

    with zipfile.ZipFile(output) as zf:
        sheet1 = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
        sheet2 = zf.read("xl/worksheets/sheet2.xml").decode("utf-8")

    assert "<t xml:space=\"preserve\">整体状态</t>" in sheet1
    assert "<t xml:space=\"preserve\">passed</t>" in sheet1
    assert "<t xml:space=\"preserve\">表名</t>" in sheet2
    assert "<t xml:space=\"preserve\">binance_usdm_funding_rate_raw</t>" in sheet2
    assert "<t xml:space=\"preserve\">2025-05-22 16:00:00</t>" in sheet2


def test_describes_known_missing_source_row_reason():
    module = _load_script_module()

    assert module._describe_difference("missing_source_row", "timestamp") == (
        "DB 中存在该 key，但源接口未返回对应行，需确认第三方接口口径或 DB 是否保留了旧数据。"
    )


def test_describes_unknown_reason_as_unclassified():
    module = _load_script_module()

    assert module._describe_difference("new_reason", "funding_time") == (
        "未归类异常；字段 funding_time 的异常类型为 new_reason。"
    )


def test_default_output_paths_follow_db_accuracy_allure_subdirectory(tmp_path, monkeypatch):
    module = _load_script_module()
    allure_root = tmp_path / "allure-results"
    reports_dir = tmp_path / "reports"
    source_path = allure_root / "db_accuracy" / "binance_kline_all_future_raw_1h" / "run-attachment.json"
    payload = {
        "passed": False,
        "tables": [
            {
                "table": "binance_kline_all_future_raw_1h",
                "passed": False,
                "windows_checked": 0,
                "db_rows_checked": 0,
                "source_rows_checked": 0,
                "differences": [],
            }
        ],
    }
    monkeypatch.setattr(module, "DEFAULT_DB_ACCURACY_ALLURE_ROOT", allure_root / "db_accuracy")

    stamped, latest = module.build_default_output_paths(payload, reports_dir, source_path=source_path)

    assert stamped.parent == reports_dir / "db_accuracy" / "binance_kline_all_future_raw_1h"
    assert latest == reports_dir / "db_accuracy" / "binance_kline_all_future_raw_1h" / "db_accuracy_allure_latest_zh.xlsx"
