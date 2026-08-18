"""pytest 全局 Fixture、环境选择和测试标记注册。"""

from __future__ import annotations

import pytest

from config.settings import Settings, SettingsLoader


def pytest_addoption(parser: pytest.Parser) -> None:
    """注册测试运行时的环境选择参数。

    参数 ``parser`` 是 pytest 提供的命令行参数解析器。
    不返回值；新增 ``--env`` 参数，默认使用 ``test`` 环境配置。
    """

    parser.addoption(
        "--env",
        action="store",
        default=None,
        help="Environment configuration name under config/, for example test or staging.",
    )


def pytest_configure(config: pytest.Config) -> None:
    """向 pytest 注册框架支持的测试标记。

    参数 ``config`` 是当前 pytest 配置对象。
    不返回值；使未在 pyproject 中声明时的标记也能在 pytest 运行中被识别。
    """

    config.addinivalue_line("markers", "smoke: core checks that do not require a real environment")
    config.addinivalue_line("markers", "regression: repeatable regression coverage")
    config.addinivalue_line("markers", "integration: cases that require a configured external API or database")


@pytest.fixture(scope="session")
def settings(pytestconfig: pytest.Config) -> Settings:
    """加载当前 pytest 命令选择的环境配置。

    参数 ``pytestconfig`` 提供 ``--env`` 命令行选项。
    返回一个会话级 ``Settings``；配置错误时测试在执行前失败。
    """

    environment = pytestconfig.getoption("--env")
    return SettingsLoader.load(environment=str(environment) if environment else None)
