import json

import polars as pl

from tests.db_accuracy.datacompy_engine import DataComPyEngine


JOIN_COLUMNS = ("symbol", "interval", "timestamp")


def test_datacompy_engine_passes_identical_frames(tmp_path):
    frame = pl.DataFrame(
        {
            "symbol": ["BTCUSDT"],
            "interval": ["1m"],
            "timestamp": ["1704067200000"],
            "open": ["1"],
            "close": ["2"],
        }
    )
    engine = DataComPyEngine(report_root=tmp_path)

    result = engine.compare(
        shard_label="symbol=BTCUSDT,interval=1m",
        partition_label="1704067200000-1704153599999",
        db_frame=frame,
        source_frame=frame,
        join_columns=JOIN_COLUMNS,
    )

    assert result.status == "passed"
    assert result.differences == 0
    assert result.report_path is not None
    assert (tmp_path / result.report_path).exists()


def test_datacompy_engine_writes_diff_for_missing_rows(tmp_path):
    db_frame = pl.DataFrame(
        {
            "symbol": ["BTCUSDT"],
            "interval": ["1m"],
            "timestamp": ["1704067200000"],
            "open": ["1"],
            "close": ["2"],
        }
    )
    source_frame = pl.DataFrame(
        schema={
            "symbol": pl.String,
            "interval": pl.String,
            "timestamp": pl.String,
            "open": pl.String,
            "close": pl.String,
        }
    )
    engine = DataComPyEngine(report_root=tmp_path)

    result = engine.compare(
        shard_label="symbol=BTCUSDT,interval=1m",
        partition_label="1704067200000-1704153599999",
        db_frame=db_frame,
        source_frame=source_frame,
        join_columns=JOIN_COLUMNS,
    )

    assert result.status == "failed"
    assert result.differences == 1
    assert result.diff_path is not None
    payload = json.loads((tmp_path / result.diff_path).read_text(encoding="utf-8"))
    assert payload["db_only_count"] == 1
    assert payload["source_only_count"] == 0
    assert payload["unequal_count"] == 0
    assert payload["db_only_sample"] == [
        {
            "symbol": "BTCUSDT",
            "interval": "1m",
            "timestamp": "1704067200000",
            "open": "1",
            "close": "2",
        }
    ]


def test_datacompy_engine_writes_diff_for_schema_less_empty_source(tmp_path):
    db_frame = pl.DataFrame(
        {
            "symbol": ["BTCUSDT"],
            "interval": ["1m"],
            "timestamp": ["1704067200000"],
            "open": ["1"],
            "close": ["2"],
        }
    )
    engine = DataComPyEngine(report_root=tmp_path)

    result = engine.compare(
        shard_label="symbol=BTCUSDT,interval=1m",
        partition_label="1704067200000-1704153599999",
        db_frame=db_frame,
        source_frame=pl.DataFrame(),
        join_columns=JOIN_COLUMNS,
    )

    assert result.status == "failed"
    assert result.db_rows == 1
    assert result.source_rows == 0
    assert result.differences == 1
    assert result.report_path is not None
    assert result.diff_path is not None
    assert (tmp_path / result.report_path).exists()
    payload = json.loads((tmp_path / result.diff_path).read_text(encoding="utf-8"))
    assert payload["db_only_count"] == 1
    assert payload["source_only_count"] == 0
    assert payload["unequal_count"] == 0


def test_datacompy_engine_passes_schema_less_empty_frames_with_artifacts(tmp_path):
    engine = DataComPyEngine(report_root=tmp_path)

    result = engine.compare(
        shard_label="symbol=BTCUSDT,interval=1m",
        partition_label="1704067200000-1704153599999",
        db_frame=pl.DataFrame(),
        source_frame=pl.DataFrame(),
        join_columns=JOIN_COLUMNS,
    )

    assert result.status == "passed"
    assert result.db_rows == 0
    assert result.source_rows == 0
    assert result.differences == 0
    assert result.report_path is not None
    assert result.diff_path is not None
    assert (tmp_path / result.report_path).exists()
    payload = json.loads((tmp_path / result.diff_path).read_text(encoding="utf-8"))
    assert payload["db_only_count"] == 0
    assert payload["source_only_count"] == 0
    assert payload["unequal_count"] == 0


def test_datacompy_engine_fails_common_payload_mismatch_with_stable_join(tmp_path):
    db_frame = pl.DataFrame(
        {
            "symbol": ["BTCUSDT"],
            "interval": ["1m"],
            "timestamp": ["1704067200000"],
            "timestamp__compare": ["1704067200000"],
            "open": ["1"],
        }
    )
    source_frame = pl.DataFrame(
        {
            "symbol": ["BTCUSDT"],
            "interval": ["1m"],
            "timestamp": ["1704067200000"],
            "timestamp__compare": ["1704067201000"],
            "open": ["1"],
        }
    )
    engine = DataComPyEngine(report_root=tmp_path)

    result = engine.compare(
        shard_label="symbol=BTCUSDT,interval=1m",
        partition_label="1704067200000-1704153599999",
        db_frame=db_frame,
        source_frame=source_frame,
        join_columns=JOIN_COLUMNS,
    )

    assert result.status == "failed"
    assert result.differences == 1
    assert result.diff_path is not None
    payload = json.loads((tmp_path / result.diff_path).read_text(encoding="utf-8"))
    assert payload["db_only_count"] == 0
    assert payload["source_only_count"] == 0
    assert payload["unequal_count"] == 1
    assert payload["unequal_sample"][0]["columns"] == ["timestamp__compare"]
