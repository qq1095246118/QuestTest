import ast
from pathlib import Path


class TestCaseSafetyRules:
    """因子库接口用例安全规则测试。

    请求参数:
        读取 tests/factor_library 下的 pytest case 文件源码，不请求真实接口。
    返回值:
        无返回值；pytest 根据 AST 检查结果判断写入用例是否满足自动化清理约束。
    """

    case_root = Path(__file__).resolve().parents[1]

    def test_resource_creating_cases_declare_cleanup_or_retention_policy(self):
        """验证会创建业务资源的可执行用例必须声明清理或保留策略。

        请求参数:
            扫描 tests/factor_library 下除 common 外的 test_*.py 文件。
        返回值:
            无返回值；创建资源的用例如果没有 resource_tracker、失效动作、保留说明或无条件 skip 则失败。
        """
        violations = []
        creation_calls = {
            "create_admin",
            "create_factor",
            "create_factor_evaluation_standard",
            "create_prompt",
            "create_quant_account",
            "create_role_template",
            "create_run",
            "create_sub_factor",
            "create_theme",
            "batch_upsert_summary_metrics",
            "batch_upsert_slice_metrics",
        }
        disable_calls = {
            "delete_user",
            "delete_quant_account",
            "delete_role_template",
            "delete_factor_evaluation_standard",
            "update_factor_status",
            "update_sub_factor_status",
            "update_theme_status",
            "batch_update_factor_status",
            "batch_update_sub_factor_status",
        }

        for path in self.case_root.rglob("test_*.py"):
            if "common" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                    continue
                called_names = {call.func.attr for call in ast.walk(node) if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)}
                if creation_calls.intersection(called_names):
                    if self._is_negative_case_name(node.name):
                        continue
                    arg_names = {arg.arg for arg in node.args.args}
                    docstring = ast.get_docstring(node) or ""
                    has_retention_note = "保留" in docstring or "人工" in docstring or "定时清理" in docstring
                    has_cleanup_or_disable = "resource_tracker" in arg_names or bool(disable_calls.intersection(called_names))
                    if not has_cleanup_or_disable and not has_retention_note and not self._is_unconditional_pytest_skip(node):
                        violations.append(f"{path.relative_to(self.case_root.parent)}::{node.name}")

        assert violations == []

    def test_copy_cases_can_run_when_they_use_auto_test_source_data(self):
        """验证 copy 正向用例必须使用 auto_test 源数据或显式跳过。

        请求参数:
            扫描 tests/factor_library 下除 common 外的 test_*.py 文件。
        返回值:
            无返回值；调用 copy_factors 或 copy_sub_factors 的用例必须引用 test_data_factory 或跳过。
        """
        violations = []
        copy_calls = {"copy_factors", "copy_sub_factors"}

        for path in self.case_root.rglob("test_*.py"):
            if "common" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                    continue
                called_names = {call.func.attr for call in ast.walk(node) if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)}
                arg_names = {arg.arg for arg in node.args.args}
                if copy_calls.intersection(called_names) and "test_data_factory" not in arg_names and not self._is_unconditional_pytest_skip(node):
                    violations.append(f"{path.relative_to(self.case_root.parent)}::{node.name}")

        assert violations == []

    def test_executable_case_files_do_not_extract_assertion_methods(self):
        """验证可执行接口用例文件不把断言提取成 case 内公共方法。

        请求参数:
            扫描 tests/factor_library 下除 common 外的 test_*.py 文件。
        返回值:
            无返回值；发现 def assert_* 或 self.assert_* 调用时失败。
        """
        violations = []

        for path in self.case_root.rglob("test_*.py"):
            if "common" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name.startswith("assert_"):
                    violations.append(f"{path.relative_to(self.case_root.parent)}::{node.name}")
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr.startswith("assert_")
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "self"
                ):
                    violations.append(f"{path.relative_to(self.case_root.parent)}::self.{node.func.attr}")

        assert violations == []

    def test_db_services_do_not_execute_write_sql(self):
        """验证 service 层 DB 代码只允许查询，不允许写库。

        请求参数:
            扫描 service 目录下所有 Python 文件源码。
        返回值:
            无返回值；发现 INSERT、UPDATE、DELETE 等写 SQL 关键字时失败。
        """
        violations = []
        write_keywords = ("INSERT", "UPDATE", "DELETE", "REPLACE", "TRUNCATE", "DROP", "ALTER")
        service_root = self.case_root.parents[1] / "service"

        for path in service_root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                normalized = node.value.strip().upper()
                is_sql_like = "\n" in node.value or " " in normalized
                if is_sql_like and normalized.startswith(write_keywords):
                    violations.append(f"{path.relative_to(service_root.parent)} contains write SQL")

        assert violations == []

    def _is_negative_case_name(self, name: str) -> bool:
        """判断用例名称是否属于负向参数校验。

        请求参数:
            name: pytest 测试函数名称。
        返回值:
            bool，名称包含常见负向关键词时返回 True。
        """
        negative_keywords = ("missing", "invalid", "nonexistent", "duplicate", "wrong", "fails", "fail")
        return any(keyword in name for keyword in negative_keywords)

    def test_task_6_remaining_cases_have_explicit_test_methods(self):
        """验证 Task 6 收尾用例有明确 pytest 落点。

        请求参数:
            扫描 factor 模块 case 文件中的测试函数名称。
        返回值:
            无返回值；SF-14 和 FM-05 缺少显式测试函数时失败。
        """
        expected_methods = {
            "test_sf_14_get_refresh_status_success_when_refresh_accepted",
            "test_fm_05_factor_mining_notification_valid_payload_success_with_selected_run_id",
        }
        discovered_methods = set()

        for path in (self.case_root / "factor").rglob("test_*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                    discovered_methods.add(node.name)

        assert expected_methods.issubset(discovered_methods)

    def _is_unconditional_pytest_skip(self, node: ast.FunctionDef) -> bool:
        """判断用例开头是否直接调用 pytest.skip。

        请求参数:
            node: 当前被扫描的 pytest 用例函数 AST 节点。
        返回值:
            bool，首条有效语句是 pytest.skip 时返回 True。
        """
        for statement in node.body:
            if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
                continue
            return (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Attribute)
                and isinstance(statement.value.func.value, ast.Name)
                and statement.value.func.value.id == "pytest"
                and statement.value.func.attr == "skip"
            )
        return False
