from pathlib import Path


pytest_plugins = ("pytester",)


def _install_project_cli_options(pytester):
    conftest_path = Path(__file__).with_name("conftest.py")
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
