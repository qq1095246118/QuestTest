import importlib.util
import sys
from pathlib import Path

import pytest

import tests.db_accuracy.integration.test_binance_db_accuracy as db_accuracy_entry


pytest_plugins = ("pytester",)


def _install_project_cli_options(pytester):
    conftest_path = _project_conftest_path()
    pytester.makeconftest(
        f"""
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "project_conftest",
            Path({str(conftest_path)!r}),
        )
        project_conftest = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(project_conftest)
        pytest_addoption = project_conftest.pytest_addoption
        """
    )


def _project_conftest_path():
    return Path(__file__).parents[2] / "conftest.py"


def _load_project_conftest():
    conftest_path = _project_conftest_path()
    spec = importlib.util.spec_from_file_location("project_conftest_for_tests", conftest_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_cached_db_accuracy_options_are_registered(pytester):
    _install_project_cli_options(pytester)
    pytester.makepyfile(
        """
        def test_options(request):
            assert request.config.getoption("--db-accuracy-mode") == "cached"
            assert request.config.getoption("--db-accuracy-cache-root") == ".cache/custom"
            assert request.config.getoption("--db-accuracy-symbol") == ["BTCUSDT"]
            assert request.config.getoption("--db-accuracy-pair") == ["BTCUSD"]
            assert request.config.getoption("--db-accuracy-contract-type") == ["CURRENT_QUARTER"]
            assert request.config.getoption("--db-accuracy-interval") == ["1m"]
            assert request.config.getoption("--db-accuracy-start-ms") == 1704067200000
            assert request.config.getoption("--db-accuracy-end-ms") == 1704153599999
            assert request.config.getoption("--db-accuracy-partition-days") == 2
            assert request.config.getoption("--db-accuracy-refresh-cache") is True
            assert request.config.getoption("--db-accuracy-max-shards") == 20
        """
    )

    result = pytester.runpytest(
        "--db-accuracy-mode",
        "cached",
        "--db-accuracy-cache-root",
        ".cache/custom",
        "--db-accuracy-symbol",
        "BTCUSDT",
        "--db-accuracy-pair",
        "BTCUSD",
        "--db-accuracy-contract-type",
        "CURRENT_QUARTER",
        "--db-accuracy-interval",
        "1m",
        "--db-accuracy-start-ms",
        "1704067200000",
        "--db-accuracy-end-ms",
        "1704153599999",
        "--db-accuracy-partition-days",
        "2",
        "--db-accuracy-refresh-cache",
        "--db-accuracy-max-shards",
        "20",
    )

    result.assert_outcomes(passed=1)


def test_cached_db_accuracy_defaults_are_registered(pytester):
    _install_project_cli_options(pytester)
    pytester.makepyfile(
        """
        def test_defaults(request):
            assert request.config.getoption("--db-accuracy-mode") == "direct"
            assert request.config.getoption("--db-accuracy-cache-root") == ".cache/binance_accuracy"
            assert request.config.getoption("--db-accuracy-symbol") == []
            assert request.config.getoption("--db-accuracy-pair") == []
            assert request.config.getoption("--db-accuracy-contract-type") == []
            assert request.config.getoption("--db-accuracy-interval") == []
            assert request.config.getoption("--db-accuracy-start-ms") is None
            assert request.config.getoption("--db-accuracy-end-ms") is None
            assert request.config.getoption("--db-accuracy-partition-days") == 1
            assert request.config.getoption("--db-accuracy-refresh-cache") is False
            assert request.config.getoption("--db-accuracy-max-shards") == 100
        """
    )

    result = pytester.runpytest()

    result.assert_outcomes(passed=1)


def test_db_accuracy_default_allure_dir_is_scoped_to_single_table(tmp_path):
    project_conftest = _load_project_conftest()
    config = _fake_config(
        {
            "--run-db-accuracy": True,
            "--db-accuracy-table": ["binance_kline_all_future_raw_1h"],
            "--db-accuracy-mode": "direct",
        },
        allure_report_dir="./allure-results",
        cwd=tmp_path,
    )

    resolved = project_conftest.configure_db_accuracy_allure_dir(config)

    assert resolved == tmp_path / "allure-results" / "db_accuracy" / "binance_kline_all_future_raw_1h"
    assert (
        config.option.allure_report_dir
        == str(tmp_path / "allure-results" / "db_accuracy" / "binance_kline_all_future_raw_1h")
    )


def test_db_accuracy_custom_allure_dir_is_not_overridden(tmp_path):
    project_conftest = _load_project_conftest()
    config = _fake_config(
        {
            "--run-db-accuracy": True,
            "--db-accuracy-table": ["binance_kline_all_future_raw_1h"],
            "--db-accuracy-mode": "direct",
        },
        allure_report_dir=str(tmp_path / "custom-allure"),
        cwd=tmp_path,
    )

    resolved = project_conftest.configure_db_accuracy_allure_dir(config)

    assert resolved is None
    assert config.option.allure_report_dir == str(tmp_path / "custom-allure")


def test_db_accuracy_pytest_configure_scopes_default_allure_dir(pytester):
    conftest_path = _project_conftest_path()
    pytester.makeconftest(
        f"""
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "project_conftest",
            Path({str(conftest_path)!r}),
        )
        project_conftest = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(project_conftest)
        pytest_addoption = project_conftest.pytest_addoption
        pytest_configure = project_conftest.pytest_configure
        """
    )
    pytester.makepyfile(
        """
        from pathlib import Path

        def test_allure_dir(request):
            expected = Path.cwd() / "allure-results" / "db_accuracy" / "binance_kline_all_future_raw_1h"
            assert request.config.option.allure_report_dir == str(expected)
        """
    )

    result = pytester.runpytest(
        "--run-db-accuracy",
        "--db-accuracy-table",
        "binance_kline_all_future_raw_1h",
        "--alluredir=./allure-results",
    )

    result.assert_outcomes(passed=1)


def test_cached_mode_validates_single_table_before_runner_construction(monkeypatch):
    monkeypatch.setattr(
        db_accuracy_entry,
        "CachedAccuracyService",
        _runner_that_must_not_be_constructed,
    )
    monkeypatch.setattr(db_accuracy_entry.allure, "attach", _no_op_attach)

    request = _fake_request(
        {
            "--db-accuracy-mode": "cached",
            "--db-accuracy-table": [],
            "--db-accuracy-start-ms": 1704067200000,
            "--db-accuracy-end-ms": 1704153599999,
        }
    )

    with pytest.raises(
        ValueError,
        match="cached DB accuracy mode requires exactly one --db-accuracy-table",
    ):
        db_accuracy_entry.test_binance_raw_and_metadata_db_accuracy(request)


def test_cached_mode_validates_time_range_before_runner_construction(monkeypatch):
    monkeypatch.setattr(
        db_accuracy_entry,
        "CachedAccuracyService",
        _runner_that_must_not_be_constructed,
    )
    monkeypatch.setattr(db_accuracy_entry.allure, "attach", _no_op_attach)

    request = _fake_request(
        {
            "--db-accuracy-mode": "cached",
            "--db-accuracy-table": ["binance_kline_all_future_raw"],
            "--db-accuracy-start-ms": None,
            "--db-accuracy-end-ms": None,
        }
    )

    with pytest.raises(
        ValueError,
        match="start_ms and end_ms are required for cached DB accuracy comparison",
    ):
        db_accuracy_entry.test_binance_raw_and_metadata_db_accuracy(request)


def _runner_that_must_not_be_constructed(*_args, **_kwargs):
    raise AssertionError("CachedAccuracyService was constructed before validation")


def _no_op_attach(*_args, **_kwargs):
    return None


def _fake_request(options):
    defaults = {
        "--db-accuracy-cache-root": ".cache/binance_accuracy",
        "--db-accuracy-symbol": [],
        "--db-accuracy-pair": [],
        "--db-accuracy-contract-type": [],
        "--db-accuracy-interval": [],
        "--db-accuracy-partition-days": 1,
        "--db-accuracy-refresh-cache": False,
        "--db-accuracy-max-shards": 100,
        "--db-accuracy-safety-hours": 24,
    }
    defaults.update(options)
    return _FakeRequest(_FakeConfig(defaults))


def _fake_config(options, *, allure_report_dir=None, cwd=None):
    defaults = {
        "--run-db-accuracy": False,
        "--db-accuracy-table": [],
        "--db-accuracy-mode": "direct",
        "--db-accuracy-symbol": [],
        "--db-accuracy-pair": [],
        "--db-accuracy-contract-type": [],
        "--db-accuracy-interval": [],
        "--db-accuracy-start-ms": None,
        "--db-accuracy-end-ms": None,
    }
    defaults.update(options)
    return _FakeConfig(defaults, allure_report_dir=allure_report_dir, cwd=cwd)


class _FakeRequest:
    def __init__(self, config):
        self.config = config


class _FakeConfig:
    def __init__(self, options, *, allure_report_dir=None, cwd=None):
        self.options = options
        self.option = _FakeOption(allure_report_dir)
        self.cwd = Path(cwd or ".")

    def getoption(self, name):
        return self.options[name]


class _FakeOption:
    def __init__(self, allure_report_dir):
        self.allure_report_dir = allure_report_dir
