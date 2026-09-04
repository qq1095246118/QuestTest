"""项目分层和公共方法规范的离线架构守卫。"""

from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_LAYERS = ("api", "config", "db", "service", "tools")
FORBIDDEN_IMPORTS = {
    "api": {"db", "service", "tests"},
    "config": {"api", "db", "service", "tests"},
    "db": {"api", "service", "tests"},
    "service": {"tests"},
    "tools": {"api", "db", "service", "tests"},
}


class TestArchitectureRules:
    """验证架构基线中可由静态语法确定的硬性规则。"""

    def test_lower_layers_do_not_import_forbidden_higher_layers(self) -> None:
        """API、DB、配置和工具层不得反向依赖 Service 或测试层。"""

        violations: list[str] = []
        for layer in SOURCE_LAYERS:
            forbidden = FORBIDDEN_IMPORTS[layer]
            for path in sorted((PROJECT_ROOT / layer).rglob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    imported_roots: set[str] = set()
                    if isinstance(node, ast.Import):
                        imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        imported_roots.add(node.module.split(".", 1)[0])
                    for imported_root in sorted(imported_roots & forbidden):
                        violations.append(
                            f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} imports {imported_root}"
                        )

        assert violations == [], violations

    def test_public_source_methods_have_documented_typed_contracts(self) -> None:
        """源码层公共函数和方法必须声明输入、输出类型并提供 docstring。"""

        violations: list[str] = []
        for layer in SOURCE_LAYERS:
            for path in sorted((PROJECT_ROOT / layer).rglob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                functions: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        functions.append(node)
                    elif isinstance(node, ast.ClassDef):
                        functions.extend(
                            item
                            for item in node.body
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        )

                for function in functions:
                    if function.name.startswith("_"):
                        continue
                    location = f"{path.relative_to(PROJECT_ROOT)}:{function.lineno} {function.name}"
                    if not ast.get_docstring(function):
                        violations.append(f"{location} missing docstring")
                    arguments = (
                        *function.args.posonlyargs,
                        *function.args.args,
                        *function.args.kwonlyargs,
                    )
                    if any(
                        argument.arg not in {"self", "cls"} and argument.annotation is None
                        for argument in arguments
                    ):
                        violations.append(f"{location} has untyped argument")
                    if function.args.vararg and function.args.vararg.annotation is None:
                        violations.append(f"{location} has untyped *args")
                    if function.args.kwarg and function.args.kwarg.annotation is None:
                        violations.append(f"{location} has untyped **kwargs")
                    if function.returns is None:
                        violations.append(f"{location} has no return annotation")

        assert violations == [], violations
