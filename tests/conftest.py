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
    "factor_library_api": "Factor Library API",
    "live_db": "Live DB Consistency",
}

ALLURE_STORY_BY_NAME = {
    "normal": "Happy Path",
    "param_error": "Parameter Error",
    "boundary": "Boundary",
    "response": "Response Schema",
}


class AllureMetadataService:
    """Allure 元数据解析与写入服务。

    请求参数:
        不需要实例化，pytest hook 和 fixture 直接通过静态方法传入收集项或请求对象。
    返回值:
        提供用例标题、story、feature、description、环境信息和分类信息的解析/写入能力。
    """

    @staticmethod
    def extract_case_metadata(test_function):
        """从测试函数 docstring 中提取 Allure 用例元数据。

        请求参数:
            test_function: pytest 收集到的测试函数对象。
        返回值:
            三元组，依次是 case_id、测试目的、完整 docstring。
        """
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

    @staticmethod
    def story_for_item(item):
        """根据用例名称推断 Allure story。

        请求参数:
            item: pytest 收集到的测试项。
        返回值:
            Allure story 名称，未匹配关键字时返回 General。
        """
        name = item.name.lower()
        for keyword, story in ALLURE_STORY_BY_NAME.items():
            if keyword in name:
                return story
        return "General"

    @staticmethod
    def parameter_suffix(item):
        """为参数化用例生成标题后缀。

        请求参数:
            item: pytest 收集到的测试项。
        返回值:
            参数化描述字符串；非参数化用例返回空字符串。
        """
        callspec = getattr(item, "callspec", None)
        if not callspec:
            return ""

        params = ", ".join(f"{key}={value}" for key, value in callspec.params.items())
        return f" [{params}]" if params else ""

    @staticmethod
    def title_for_item(item):
        """根据 case_id、测试目的和参数生成 Allure 标题。

        请求参数:
            item: pytest 收集到的测试项。
        返回值:
            计算后的 Allure 标题字符串。
        """
        case_id, purpose, _ = AllureMetadataService.extract_case_metadata(item.function)
        if case_id:
            title = case_id
            if purpose:
                title = f"{case_id} - {purpose}"
        else:
            title = purpose or item.name

        return f"{title}{AllureMetadataService.parameter_suffix(item)}"

    @staticmethod
    def explicit_title(item):
        """读取测试项上显式设置的 Allure 标题。

        请求参数:
            item: pytest 收集到的测试项。
        返回值:
            显式标题字符串；未设置时返回空字符串。
        """
        return getattr(item.obj, "__allure_display_name__", "") or getattr(
            item.function,
            "__allure_display_name__",
            "",
        )

    @staticmethod
    def resolved_title(item):
        """解析测试项最终展示的 Allure 标题。

        请求参数:
            item: pytest 收集到的测试项。
        返回值:
            可直接写入 Allure 的标题字符串。
        """
        title = AllureMetadataService.explicit_title(item) or AllureMetadataService.title_for_item(item)
        callspec = getattr(item, "callspec", None)
        if not callspec:
            return title

        try:
            return title.format(**callspec.params)
        except Exception:
            return title

    @staticmethod
    def apply_default_title(item):
        """在 pytest 收集阶段为单条用例补充默认 Allure 标题。

        请求参数:
            item: pytest 收集到的测试项。
        返回值:
            无，副作用是给未显式设置标题的测试函数写入展示标题。
        """
        test_function = getattr(item, "function", None)
        if not test_function:
            return
        if AllureMetadataService.explicit_title(item):
            return
        test_function.__allure_display_name__ = AllureMetadataService.title_for_item(item)

    @staticmethod
    def write_case_metadata(item):
        """为单条用例写入 Allure 动态元数据。

        请求参数:
            item: pytest 当前执行的测试项。
        返回值:
            无，副作用是动态写入 Allure id、title、suite、story、feature、tag 和 severity。
        """
        case_id, _, doc = AllureMetadataService.extract_case_metadata(item.function)
        story = AllureMetadataService.story_for_item(item)
        marker_names = {marker.name for marker in item.iter_markers()}
        module_name = item.module.__name__.replace("tests.", "")

        if case_id:
            allure.dynamic.id(case_id)

        allure.dynamic.title(AllureMetadataService.resolved_title(item))
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

    @staticmethod
    def base_url_configured() -> bool:
        """判断当前环境是否配置了有效接口地址。

        请求参数:
            无，内部读取 config.settings.settings。
        返回值:
            True 表示 base_url 已配置且不是示例占位地址；配置读取失败时返回 False。
        """
        try:
            from config.settings import settings

            return bool(settings.base_url and "exchange.com" not in settings.base_url)
        except Exception:
            return False

    @staticmethod
    def write_allure_environment(report_dir: Path, env: str) -> None:
        """写入 Allure environment.properties。

        请求参数:
            report_dir: Allure 原始结果目录。
            env: pytest --env 参数值。
        返回值:
            无，副作用是在结果目录写入 environment.properties。
        """
        env_lines = {
            "Project": "QuestTest",
            "Environment": env,
            "Python": platform.python_version(),
            "Base URL Configured": str(AllureMetadataService.base_url_configured()),
        }
        environment_text = "\n".join(f"{key}={value}" for key, value in env_lines.items())
        (report_dir / "environment.properties").write_text(environment_text, encoding="utf-8")

    @staticmethod
    def write_allure_categories(report_dir: Path) -> None:
        """写入 Allure categories.json。

        请求参数:
            report_dir: Allure 原始结果目录。
        返回值:
            无，副作用是在结果目录写入 categories.json。
        """
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
        (report_dir / "categories.json").write_text(json.dumps(categories, indent=2), encoding="utf-8")


def pytest_addoption(parser):
    """注册 pytest 命令行参数。

    请求参数:
        parser: pytest 提供的命令行参数解析器。
    返回值:
        无，注册 --env 参数用于切换测试环境。
    """
    parser.addoption(
        "--env", action="store", default="test", help="Environment to run tests against (e.g., test, prod)"
    )


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    """在测试模块导入配置前写入 TEST_ENV。

    请求参数:
        config: pytest 当前运行配置。
    返回值:
        无，副作用是把 --env 的值写入环境变量 TEST_ENV。
    """
    os.environ["TEST_ENV"] = config.getoption("--env")


def pytest_collection_modifyitems(session, config, items):
    """在 pytest 收集阶段为用例补充默认 Allure 标题。

    请求参数:
        session: pytest 当前会话对象。
        config: pytest 当前运行配置。
        items: pytest 收集到的测试项列表。
    返回值:
        无，副作用是给未显式设置标题的测试函数写入展示标题。
    """
    for item in items:
        AllureMetadataService.apply_default_title(item)


@pytest.fixture(autouse=True)
def allure_case_metadata(request):
    """为每条用例写入 Allure 元数据。

    请求参数:
        request: pytest fixture request 对象。
    返回值:
        无，副作用是动态写入 Allure id、title、suite、story、feature、tag 和 severity。
    """
    AllureMetadataService.write_case_metadata(request.node)


@pytest.fixture(scope="session", autouse=True)
def set_env(request):
    """根据命令行参数设置测试环境。

    请求参数:
        request: pytest fixture request 对象。
    返回值:
        无，副作用是把 --env 的值写入环境变量 TEST_ENV 并记录日志。
    """
    env = request.config.getoption("--env")
    os.environ["TEST_ENV"] = env
    logging.info(f"Test environment set to: {env}")


def pytest_sessionfinish(session, exitstatus):
    """在测试会话结束时写入 Allure 环境和分类元数据。

    请求参数:
        session: pytest 当前会话对象。
        exitstatus: pytest 会话退出状态码。
    返回值:
        无，存在 Allure 结果目录时写入 environment.properties 和 categories.json。
    """
    allure_dir = getattr(session.config.option, "allure_report_dir", None)
    if not allure_dir:
        return

    report_dir = Path(allure_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    AllureMetadataService.write_allure_environment(report_dir, session.config.getoption("--env"))
    AllureMetadataService.write_allure_categories(report_dir)
