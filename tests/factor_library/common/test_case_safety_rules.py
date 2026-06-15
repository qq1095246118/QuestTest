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

    def test_executable_case_files_do_not_define_top_level_test_functions(self):
        """验证可执行接口用例文件必须使用传统测试类承载用例。

        请求参数:
            扫描 tests/factor_library 下除 common 外的 test_*.py 文件。
        返回值:
            无返回值；发现模块顶层 test_* 函数时失败。
        """
        violations = []

        for path in self.case_root.rglob("test_*.py"):
            if "common" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                    violations.append(f"{path.relative_to(self.case_root.parent)}::{node.name}")

        assert violations == []

    def test_non_test_helpers_do_not_contain_final_assertions(self):
        """验证业务 case 内非 test helper 不包含最终断言。

        请求参数:
            扫描 tests/factor_library 下除 common 外的 test_*.py 文件。
        返回值:
            无返回值；发现非 test 方法内调用 pytest.fail、JSON 失败输出或 assert 语句时失败。
        """
        violations = []
        forbidden_failure_helpers = {
            "fail",
            "fail_with_api_json",
            "fail_with_two_json",
            "attach_json",
        }

        for path in self.case_root.rglob("test_*.py"):
            if "common" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef) or node.name.startswith("test_"):
                    continue
                for child in ast.walk(node):
                    if isinstance(child, ast.Assert):
                        violations.append(f"{path.relative_to(self.case_root.parent)}::{node.name} assert")
                    if (
                        isinstance(child, ast.Call)
                        and isinstance(child.func, ast.Attribute)
                        and child.func.attr in forbidden_failure_helpers
                    ):
                        violations.append(f"{path.relative_to(self.case_root.parent)}::{node.name} {child.func.attr}")

        assert violations == []

    def test_executable_case_files_do_not_import_business_assertion_services(self):
        """验证可执行接口用例文件不再引用业务断言服务。

        请求参数:
            扫描 tests/factor_library 下除 common 外的 test_*.py 文件。
        返回值:
            无返回值；发现 Admin/Factor/FactorIC 业务断言服务 import 时失败。
        """
        violations = []
        blocked_import_modules = {
            "service.factor_library.admin.admin_assertions",
            "service.factor_library.auth.auth_assertions",
            "service.factor_library.factors.factor_assertions",
            "service.factor_library.factor_ic.factor_ic_assertions",
        }

        for path in self.case_root.rglob("test_*.py"):
            if "common" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and self._is_business_assertion_module(
                    node.module, blocked_import_modules
                ):
                    violations.append(f"{path.relative_to(self.case_root.parent)}::{node.module}")
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if self._is_business_assertion_module(alias.name, blocked_import_modules):
                            violations.append(f"{path.relative_to(self.case_root.parent)}::{alias.name}")

        assert violations == []

    def test_service_layer_does_not_contain_business_assertion_services(self):
        """验证 service 层不再保留业务断言服务文件。

        请求参数:
            扫描 service/factor_library 下的 Python 文件。
        返回值:
            无返回值；发现 *_assertions.py 业务断言文件时失败。
        """
        service_root = self.case_root.parents[1] / "service" / "factor_library"
        assertion_files = sorted(path.relative_to(service_root.parent) for path in service_root.rglob("*_assertions.py"))

        assert assertion_files == []

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

    def test_wrapper_contracts_do_not_include_deferred_interfaces(self):
        """验证 wrapper 契约测试不再覆盖本轮暂缓接口。

        请求参数:
            读取 tests/factor_library/common/test_api_wrappers.py 源码。
        返回值:
            无返回值；发现本轮暂缓接口 wrapper 名称仍在契约测试中时失败。
        """
        wrapper_test_path = self.case_root / "common" / "test_api_wrappers.py"
        source = wrapper_test_path.read_text(encoding="utf-8")
        blocked_names = {
            '"list_invite_codes"',
            '"list_prompts"',
            '"create_prompt"',
            '"update_prompt"',
            '"update_agent_factory_config"',
            '"get_agent_factory_config"',
            '"refresh_sub_factor"',
            '"get_sub_factor_refresh"',
            '"get_factor_by_symbol"',
            '"get_sub_factor_by_symbol"',
            '"list_summary_metrics"',
        }

        violations = sorted(name.strip('"') for name in blocked_names if name in source)

        assert violations == []

    def test_current_scope_does_not_collect_deferred_business_modules(self):
        """验证本轮暂不覆盖的业务模块没有可执行接口用例。

        请求参数:
            扫描 tests/factor_library 下的 test_*.py 文件。
        返回值:
            无返回值；发现 Quantitative_Trading、Chat、Runs 可执行用例时失败。
        """
        blocked_parts = {"Quantitative_Trading", "Chat", "Runs"}
        violations = []
        for path in self.case_root.rglob("test_*.py"):
            if blocked_parts.intersection(path.parts):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef) and node.name.startswith("test_") and not self._is_unconditional_pytest_skip(node):
                        violations.append(f"{path.relative_to(self.case_root.parent)}::{node.name}")

        assert violations == []

    def test_current_scope_excludes_deferred_interface_calls(self):
        """验证本轮跳过的接口没有出现在业务用例调用中。

        请求参数:
            扫描 tests/factor_library 下除 common 外的 test_*.py 文件源码。
        返回值:
            无返回值；发现跳过接口 wrapper 调用时失败。
        """
        blocked_calls = {
            "list_invite_codes",
            "list_prompts",
            "create_prompt",
            "update_prompt",
            "update_agent_factory_config",
            "get_agent_factory_config",
            "refresh_sub_factor",
            "get_sub_factor_refresh",
            "get_factor_by_symbol",
            "get_sub_factor_by_symbol",
        }
        violations = []
        for path in self.case_root.rglob("test_*.py"):
            if "common" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in blocked_calls:
                    violations.append(f"{path.relative_to(self.case_root.parent)}::{node.func.attr}")

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

    def _is_business_assertion_module(self, module_name: str | None, blocked_modules: set[str]) -> bool:
        """判断 import 路径是否指向业务断言服务。

        请求参数:
            module_name: AST 中解析出的 import 模块路径。
            blocked_modules: 历史业务断言服务完整模块名集合。
        返回值:
            bool，命中历史模块名或 service.factor_library 下 *_assertions 模块时返回 True。
        """
        if not module_name:
            return False
        return module_name in blocked_modules or (
            module_name.startswith("service.factor_library.") and module_name.endswith("_assertions")
        )
