"""按环境运行 pytest 并输出 JUnit XML 报告。"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from config.settings import SettingsLoader


class TestRunner:
    """构造并运行项目统一的 pytest 命令。"""

    def run(self, environment: str | None, marker: str | None, report_path: Path | None) -> int:
        """运行测试并写入 JUnit XML 报告。

        参数 ``environment`` 是可选的 ``config`` 环境名，``marker`` 是可选 pytest 标记表达式，``report_path`` 是可选报告路径。
        返回 pytest 进程退出码；未传报告路径时读取环境配置中的 ``reports.junit_path``，报告目录会在运行前自动创建。
        """

        project_root = Path(__file__).resolve().parents[1]
        settings = SettingsLoader.load(environment=environment, project_root=project_root)
        selected_report_path = report_path or Path(settings.reports.junit_path)
        if not selected_report_path.is_absolute():
            selected_report_path = project_root / selected_report_path
        selected_report_path.parent.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-m", "pytest", "--junitxml", str(selected_report_path)]
        if environment:
            command.extend(["--env", environment])
        if marker:
            command.extend(["-m", marker])
        return subprocess.run(command, cwd=project_root, check=False).returncode


def parse_arguments() -> argparse.Namespace:
    """解析测试脚本的命令行参数。

    不接收参数。
    返回包含环境名、可选标记和报告路径的 ``argparse.Namespace``。
    """

    parser = argparse.ArgumentParser(description="Run API automation tests with a selected environment")
    parser.add_argument("--env", help="Configuration name under config/; defaults to AUTOMATION_ENV or test")
    parser.add_argument("--marker", help="Optional pytest marker expression")
    parser.add_argument("--report", help="JUnit XML output path; defaults to reports.junit_path in config")
    return parser.parse_args()


def main() -> int:
    """执行命令行测试入口。

    不接收参数。
    返回 pytest 的进程退出码，供本地命令行和 CI 直接使用。
    """

    arguments = parse_arguments()
    report_path = Path(arguments.report) if arguments.report else None
    return TestRunner().run(arguments.env, arguments.marker, report_path)


if __name__ == "__main__":
    raise SystemExit(main())
