import inspect
import json
import logging
import os
import platform
import re
from pathlib import Path

import allure
import pytest


ALLURE_PARENT_SUITE = "QuestTest API Automation"

ALLURE_MARKER_FEATURES = {
    "kline_api": "Kline API",
    "dqc": "Data Quality Control",
    "logic": "Financial Logic",
    "performance": "Performance Baseline",
    "db_accuracy": "Database Source Accuracy",
}

ALLURE_STORY_BY_NAME = {
    "normal": "Happy Path",
    "param_error": "Parameter Error",
    "boundary": "Boundary",
    "response": "Response Schema",
    "performance": "Performance",
    "db_consistency": "Database Consistency",
    "data_quality": "Data Quality",
}

DEFAULT_ALLURE_DIR = Path("allure-results")
DB_ACCURACY_ALLURE_ROOT = DEFAULT_ALLURE_DIR / "db_accuracy"
DEFAULT_ALLURE_DIR_NAMES = {"allure-results", "./allure-results"}


def pytest_addoption(parser):
    parser.addoption(
        "--env", action="store", default="test", help="Environment to run tests against (e.g., test, prod)"
    )
    parser.addoption(
        "--run-db-accuracy",
        action="store_true",
        default=False,
        help="Run manual Binance database-to-source accuracy validation",
    )
    parser.addoption(
        "--db-accuracy-safety-hours",
        action="store",
        type=int,
        default=24,
        help="Safety window in hours for database accuracy validation",
    )
    parser.addoption(
        "--db-accuracy-table",
        action="append",
        default=[],
        help="Limit database accuracy validation to one or more tables",
    )
    parser.addoption(
        "--db-accuracy-mode",
        action="store",
        choices=("direct", "cached"),
        default="direct",
        help="DB accuracy execution mode: direct or cached",
    )
    parser.addoption(
        "--db-accuracy-cache-root",
        action="store",
        default=".cache/binance_accuracy",
        help="Local cache root for cached Binance source data",
    )
    parser.addoption(
        "--db-accuracy-symbol",
        action="append",
        default=[],
        help="Limit cached DB accuracy validation to one or more symbols",
    )
    parser.addoption(
        "--db-accuracy-pair",
        action="append",
        default=[],
        help="Limit cached DB accuracy validation to one or more delivery pairs",
    )
    parser.addoption(
        "--db-accuracy-contract-type",
        action="append",
        default=[],
        help="Limit cached DB accuracy validation to one or more contract types",
    )
    parser.addoption(
        "--db-accuracy-interval",
        action="append",
        default=[],
        help="Limit cached DB accuracy validation to one or more intervals",
    )
    parser.addoption(
        "--db-accuracy-start-ms",
        action="store",
        type=int,
        default=None,
        help="Inclusive start timestamp in milliseconds for cached DB accuracy validation",
    )
    parser.addoption(
        "--db-accuracy-end-ms",
        action="store",
        type=int,
        default=None,
        help="Inclusive end timestamp in milliseconds for cached DB accuracy validation",
    )
    parser.addoption(
        "--db-accuracy-partition-days",
        action="store",
        type=int,
        default=1,
        help="Time partition size in days for cached DB accuracy validation",
    )
    parser.addoption(
        "--db-accuracy-refresh-cache",
        action="store_true",
        default=False,
        help="Refresh cached Binance source partitions before comparing",
    )
    parser.addoption(
        "--db-accuracy-max-shards",
        action="store",
        type=int,
        default=100,
        help="Maximum DB-discovered market shards for cached DB accuracy validation",
    )

@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    """
    Set TEST_ENV before test modules import config.settings.
    """
    configure_db_accuracy_allure_dir(config)
    os.environ["TEST_ENV"] = config.getoption("--env")


def configure_db_accuracy_allure_dir(config):
    """
    Isolate DB accuracy Allure output by command/table when the default Allure
    directory is in use. Explicit --alluredir values are respected.
    """
    if not config.getoption("--run-db-accuracy"):
        return None

    current = getattr(config.option, "allure_report_dir", None)
    if not current or not _is_default_allure_dir(current, _config_cwd(config)):
        return None

    report_dir = _config_cwd(config) / DB_ACCURACY_ALLURE_ROOT / _db_accuracy_run_slug(config)
    config.option.allure_report_dir = str(report_dir)
    return report_dir


def _config_cwd(config) -> Path:
    return Path(getattr(config, "cwd", Path.cwd()))


def _is_default_allure_dir(value, cwd: Path) -> bool:
    text = str(value).rstrip("/")
    if text in DEFAULT_ALLURE_DIR_NAMES:
        return True

    path = Path(text)
    if not path.is_absolute():
        path = cwd / path
    return path == cwd / DEFAULT_ALLURE_DIR


def _db_accuracy_run_slug(config) -> str:
    tables = config.getoption("--db-accuracy-table") or []
    mode = config.getoption("--db-accuracy-mode")
    if len(tables) == 1:
        parts = [tables[0]]
    elif tables:
        parts = ["multi", *tables]
    else:
        parts = ["all_tables"]

    if mode == "cached":
        parts.append("cached")
        parts.extend(_option_values_for_slug(config, "--db-accuracy-symbol", "symbol"))
        parts.extend(_option_values_for_slug(config, "--db-accuracy-pair", "pair"))
        parts.extend(_option_values_for_slug(config, "--db-accuracy-contract-type", "contract"))
        parts.extend(_option_values_for_slug(config, "--db-accuracy-interval", "interval"))
        for option, label in [
            ("--db-accuracy-start-ms", "start"),
            ("--db-accuracy-end-ms", "end"),
        ]:
            value = config.getoption(option)
            if value is not None:
                parts.append(f"{label}_{value}")

    return _safe_path_component("__".join(str(part) for part in parts if part))


def _option_values_for_slug(config, option: str, label: str) -> list[str]:
    values = config.getoption(option) or []
    return [f"{label}_{value}" for value in values]


def _safe_path_component(value: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", value).strip("._")
    return (safe or "db_accuracy")[:180]


def _extract_case_metadata(test_function):
    doc = inspect.getdoc(test_function) or ""
    case_id = ""
    purpose = ""

    for line in doc.splitlines():
        text = line.strip()
        if text.startswith("Case ID:"):
            case_id = text.replace("Case ID:", "", 1).strip()
        elif text.startswith("测试目的:"):
            purpose = text.replace("测试目的:", "", 1).strip()

    if not purpose and doc:
        purpose = doc.splitlines()[0].strip()

    return case_id, purpose, doc


def _allure_story_for_item(item):
    name = item.name.lower()
    for keyword, story in ALLURE_STORY_BY_NAME.items():
        if keyword in name:
            return story
    return "General"


def _parameter_suffix(item):
    callspec = getattr(item, "callspec", None)
    if not callspec:
        return ""

    params = ", ".join(f"{key}={value}" for key, value in callspec.params.items())
    return f" [{params}]" if params else ""


def _allure_title_for_item(item):
    case_id, purpose, _ = _extract_case_metadata(item.function)
    if case_id:
        title = case_id
        if purpose:
            title = f"{case_id} - {purpose}"
    else:
        title = purpose or item.name

    return f"{title}{_parameter_suffix(item)}"


def _explicit_allure_title(item):
    return getattr(item.obj, "__allure_display_name__", "")


def _resolved_allure_title(item):
    title = _explicit_allure_title(item) or _allure_title_for_item(item)
    callspec = getattr(item, "callspec", None)
    if not callspec:
        return title

    try:
        return title.format(**callspec.params)
    except Exception:
        return title


def pytest_collection_modifyitems(session, config, items):
    """
    Set fallback Allure titles during collection so reports do not fall back to function names.
    """
    for item in items:
        test_function = getattr(item, "function", None)
        if not test_function:
            continue
        if _explicit_allure_title(item):
            continue
        item.obj.__allure_display_name__ = _allure_title_for_item(item)

    if config.getoption("--run-db-accuracy"):
        return

    skip_db_accuracy = pytest.mark.skip(
        reason="database accuracy validation requires --run-db-accuracy"
    )
    for item in items:
        if item.get_closest_marker("db_accuracy") is not None:
            item.add_marker(skip_db_accuracy)


@pytest.fixture(autouse=True)
def allure_case_metadata(request):
    """
    Populate Allure metadata from pytest markers and each case docstring.
    """
    item = request.node
    case_id, _, doc = _extract_case_metadata(item.function)
    story = _allure_story_for_item(item)
    marker_names = {marker.name for marker in item.iter_markers()}
    module_name = item.module.__name__.replace("tests.", "")

    if case_id:
        allure.dynamic.id(case_id)

    allure.dynamic.title(_resolved_allure_title(item))
    if doc:
        allure.dynamic.description(doc)

    allure.dynamic.parent_suite(ALLURE_PARENT_SUITE)
    allure.dynamic.suite(module_name)
    allure.dynamic.sub_suite(story)
    allure.dynamic.story(story)

    features = [
        feature
        for marker, feature in ALLURE_MARKER_FEATURES.items()
        if marker in marker_names
    ]
    if not features:
        features = ["API Tests"]

    for feature in features:
        allure.dynamic.feature(feature)

    for marker_name in sorted(marker_names):
        allure.dynamic.tag(marker_name)

    allure.dynamic.severity(allure.severity_level.NORMAL)

@pytest.fixture(scope="session", autouse=True)
def set_env(request):
    """
    Dynamically switch environment configurations based on CLI args.
    """
    env = request.config.getoption("--env")
    os.environ["TEST_ENV"] = env
    logging.info(f"Test environment set to: {env}")

def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """
    Hook to generate summary and send IM alerts (WeCom/DingTalk) after tests finish.
    """
    passed = len(terminalreporter.stats.get('passed', []))
    failed = len(terminalreporter.stats.get('failed', []))
    skipped = len(terminalreporter.stats.get('skipped', []))
    
    # Placeholder for IM notification logic
    alert_msg = f"API AutoTest Finished. Passed: {passed}, Failed: {failed}, Skipped: {skipped}"
    print(f"\n[Alert Hook] {alert_msg}")
    # send_im_alert(alert_msg)


def pytest_sessionfinish(session, exitstatus):
    """
    Add environment and category metadata to the generated Allure result set.
    """
    allure_dir = getattr(session.config.option, "allure_report_dir", None)
    if not allure_dir:
        return

    report_dir = Path(allure_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    try:
        from config.settings import settings

        base_url_configured = bool(
            settings.base_url and "exchange.com" not in settings.base_url
        )
    except Exception:
        base_url_configured = False

    env_lines = {
        "Project": "QuestTest",
        "Environment": session.config.getoption("--env"),
        "Python": platform.python_version(),
        "Base URL Configured": str(base_url_configured),
    }
    environment_text = "\n".join(
        f"{key}={value}" for key, value in env_lines.items()
    )
    (report_dir / "environment.properties").write_text(
        environment_text,
        encoding="utf-8",
    )

    categories = [
        {
            "name": "Assertion Failure",
            "matchedStatuses": ["failed"],
            "messageRegex": ".*AssertionError.*",
        },
        {
            "name": "HTTP Error",
            "matchedStatuses": ["failed", "broken"],
            "traceRegex": ".*HTTPError.*",
        },
        {
            "name": "Dependency Or Import Error",
            "matchedStatuses": ["broken"],
            "traceRegex": ".*(ModuleNotFoundError|ImportError).*",
        },
    ]
    (report_dir / "categories.json").write_text(
        json.dumps(categories, indent=2),
        encoding="utf-8",
    )
