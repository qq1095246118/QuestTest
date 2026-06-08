import inspect
import json
import logging
import os
import platform
from pathlib import Path

import allure
import pytest


ALLURE_PARENT_SUITE = "QuestTest API Automation"

ALLURE_MARKER_FEATURES = {
    "kline_api": "Kline API",
    "dqc": "Data Quality Control",
    "logic": "Financial Logic",
    "performance": "Performance Baseline",
}

ALLURE_STORY_BY_NAME = {
    "normal": "Happy Path",
    "param_error": "Parameter Error",
    "boundary": "Boundary",
    "response": "Response Schema",
    "performance": "Performance",
    "data_quality": "Data Quality",
}

def pytest_addoption(parser):
    parser.addoption(
        "--env", action="store", default="test", help="Environment to run tests against (e.g., test, prod)"
    )

@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    """
    Set TEST_ENV before test modules import config.settings.
    """
    os.environ["TEST_ENV"] = config.getoption("--env")


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
