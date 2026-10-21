"""持久化量化看板数据集的安全造数计划工具。

这个模块只负责生成并校验一次性数据集的造数计划。真实的策略事实由策略侧
源库产生，当前仓库没有拿到 NT/PG 的正式 DDL 和首次全量拉取入口，因此默认
只读检查 MySQL 身份投影和源事件投影；不会写入派生结果表，也不会调用重算。

使用方式::

    python scripts/persistent_dataset_seed.py plan \
        --env test --manifest tests/data/persistent_dashboard_dataset_manifest.json

``apply`` 仍然要求开发侧提供已经确认的 source adapter 和 full-pull 命令，
否则以 ``SEED_CONTRACT_BLOCKED`` 退出。这样可以把“工具存在”与“已经能把
数据送进真实计算链”区分开，避免再次只写 read view 后误报通过。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import SettingsLoader
from db.client import DatabaseClient


class SeedContractBlocked(RuntimeError):
    """策略侧源表或全量发布契约未确认，不能执行写入。"""


# These are identity/source projections observed in the test MySQL database.  The
# order is also the safe parent-before-child order for a future adapter.
SOURCE_TABLES: tuple[str, ...] = (
    "quant_accounts",
    "quant_account_profiles",
    "quant_strategy_definitions",
    "quant_strategy_account_runs",
    "quant_nt_execution_accounts",
    "quant_nt_strategy_accounts",
    "quant_nt_account_strategy_assignments",
    "quant_nt_trading_history_events",
    "quant_nt_trading_snapshots",
)

# These tables are produced by the collector/metric pipeline.  A seed adapter is
# forbidden from writing them because doing so bypasses the behaviour under test.
DERIVED_TABLES: frozenset[str] = frozenset(
    {
        "quant_strategy_performance_samples",
        "quant_position_snapshot_batches",
        "quant_position_snapshots",
        "quant_trading_events",
        "quant_account_snapshots",
        "quant_segmented_nt_facts",
        "quant_segmented_performance_checkpoints",
        "quant_segmented_performance_generations",
        "quant_read_views",
        "quant_collection_batches",
    }
)


@dataclass(frozen=True)
class SeedManifest:
    """描述一次性数据集的稳定身份和覆盖要求。"""

    dataset_tag: str
    version: str
    accounts: tuple[str, ...]
    strategies: tuple[str, ...]
    source_tables: tuple[str, ...]
    full_pull_command: str | None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "SeedManifest":
        """从 JSON/YAML 映射读取 manifest；字段缺失时抛出 ``ValueError``。"""

        tag = str(raw.get("dataset_tag") or "").strip()
        version = str(raw.get("version") or "").strip()
        if not tag or not version:
            raise ValueError("manifest requires non-empty dataset_tag and version")
        def _ids(name: str) -> tuple[str, ...]:
            values = raw.get(name, ())
            if not isinstance(values, (list, tuple)):
                raise ValueError(f"manifest field {name} must be a list")
            result = tuple(str(value).strip() for value in values if str(value).strip())
            if not result:
                raise ValueError(f"manifest field {name} must not be empty")
            return result

        requested_tables = raw.get("source_tables", SOURCE_TABLES)
        if not isinstance(requested_tables, (list, tuple)):
            raise ValueError("manifest field source_tables must be a list")
        source_tables = tuple(str(value).strip() for value in requested_tables if str(value).strip())
        forbidden = sorted(set(source_tables) & DERIVED_TABLES)
        if forbidden:
            raise ValueError("manifest source_tables contains derived table(s): " + ",".join(forbidden))
        return cls(
            dataset_tag=tag,
            version=version,
            accounts=_ids("accounts"),
            strategies=_ids("strategies"),
            source_tables=source_tables,
            full_pull_command=(str(raw["full_pull_command"]).strip() if raw.get("full_pull_command") else None),
        )


@dataclass(frozen=True)
class SeedPlan:
    """只读校验后的造数计划及当前数据库状态。"""

    manifest: SeedManifest
    available_tables: tuple[str, ...]
    missing_source_tables: tuple[str, ...]
    marker_rows: int
    derived_rows: Mapping[str, int]

    @property
    def ready_for_source_adapter(self) -> bool:
        """返回源投影表齐全且尚未存在同标签身份数据。"""

        return not self.missing_source_tables and self.marker_rows == 0

    def as_dict(self) -> dict[str, Any]:
        """返回不含凭据的 JSON 可序列化结果。"""

        return {
            "dataset_tag": self.manifest.dataset_tag,
            "version": self.manifest.version,
            "accounts": list(self.manifest.accounts),
            "strategies": list(self.manifest.strategies),
            "source_tables": list(self.manifest.source_tables),
            "available_tables": list(self.available_tables),
            "missing_source_tables": list(self.missing_source_tables),
            "marker_rows": self.marker_rows,
            "derived_rows": dict(self.derived_rows),
            "ready_for_source_adapter": self.ready_for_source_adapter,
            "full_pull_command_configured": bool(self.manifest.full_pull_command),
        }


class PersistentDatasetSeedTool:
    """在测试环境中读取并检查一次性数据集造数前置。"""

    def __init__(self, client: DatabaseClient, *, environment: str) -> None:
        """保存只读数据库客户端并拒绝非测试环境。"""

        if environment != "test":
            raise SeedContractBlocked("SEED_ENVIRONMENT_BLOCKED: only --env test is allowed")
        self._client = client

    def plan(self, manifest: SeedManifest) -> SeedPlan:
        """查询表结构、标签残留和派生行数，不执行任何写入。"""

        table_rows = self._client.fetch_all(
            "SELECT TABLE_NAME AS table_name FROM information_schema.tables "
            "WHERE table_schema = DATABASE()"
        )
        available = tuple(sorted(str(row.get("table_name") or "") for row in table_rows))
        available_set = set(available)
        missing = tuple(sorted(set(manifest.source_tables) - available_set))
        marker_rows = 0
        marker = manifest.dataset_tag + "%"
        # Dataset identity is carried in the source projection's stable IDs or
        # sync_generation. No broad name matching is used.
        if "quant_strategy_definitions" in available_set:
            row = self._client.fetch_one(
                "SELECT COUNT(*) AS n FROM quant_strategy_definitions "
                "WHERE id LIKE %s OR code LIKE %s OR name LIKE %s",
                (marker, marker, marker),
            )
            marker_rows += int((row or {}).get("n") or 0)
        if "quant_nt_execution_accounts" in available_set:
            row = self._client.fetch_one(
                "SELECT COUNT(*) AS n FROM quant_nt_execution_accounts "
                "WHERE nt_account_id LIKE %s OR sync_generation LIKE %s",
                (marker, marker),
            )
            marker_rows += int((row or {}).get("n") or 0)
        derived: dict[str, int] = {}
        for table in sorted(DERIVED_TABLES):
            if table not in available_set:
                continue
            # Prefer indexed identity columns.  Casting large JSON payloads made
            # the plan scan the whole production-sized projection tables and
            # could block the test runner; payload is not an identity key.
            try:
                columns = self._client.fetch_all(
                    "SELECT COLUMN_NAME AS column_name FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = %s",
                    (table,),
                )
                candidates = [
                    str(row.get("column_name") or "")
                    for row in columns
                    if str(row.get("column_name") or "") in {
                        "definition_id", "assignment_id", "sync_generation", "source_generation",
                        "source_key", "run_key", "namespace",
                    }
                ]
                if not candidates:
                    continue
                predicates = " OR ".join(f"`{column}` LIKE %s" for column in candidates)
                row = self._client.fetch_one(
                    f"SELECT COUNT(*) AS n FROM `{table}` WHERE {predicates}",
                    tuple(manifest.dataset_tag + "%" for _ in candidates),
                )
            except Exception:
                row = None
            if row is not None:
                derived[table] = int(row.get("n") or 0)
        return SeedPlan(manifest, available, missing, marker_rows, derived)

    def apply(self, manifest: SeedManifest) -> None:
        """拒绝未绑定 source adapter/full-pull 的写入请求。"""

        plan = self.plan(manifest)
        if os.getenv("AUTOMATION_PERSISTENT_SEED_APPLY") != "1":
            raise SeedContractBlocked(
                "SEED_CONTRACT_BLOCKED: apply requires AUTOMATION_PERSISTENT_SEED_APPLY=1 "
                "and an approved strategy-side source adapter"
            )
        if not plan.ready_for_source_adapter:
            raise SeedContractBlocked(
                "SEED_CONTRACT_BLOCKED: source schema incomplete or dataset marker already exists; "
                "use plan output before applying"
            )
        if not manifest.full_pull_command:
            raise SeedContractBlocked(
                "SEED_CONTRACT_BLOCKED: manifest.full_pull_command is not confirmed; "
                "source rows must be followed by the developer full-pull/publish job"
            )
        raise SeedContractBlocked(
            "SEED_CONTRACT_BLOCKED: no approved strategy-side adapter is installed; "
            "this repository must not write derived dashboard tables"
        )


def _load_manifest(path: Path) -> SeedManifest:
    """读取 JSON 或 YAML manifest。"""

    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as error:
            raise ValueError("YAML manifest requires PyYAML") from error
        value = yaml.safe_load(text)
    else:
        value = json.loads(text)
    if not isinstance(value, Mapping):
        raise ValueError("manifest root must be an object")
    return SeedManifest.from_mapping(value)


def main(argv: list[str] | None = None) -> int:
    """执行 ``plan`` 或受保护的 ``apply`` 子命令。"""

    parser = argparse.ArgumentParser(description="persistent dashboard dataset seed gate")
    parser.add_argument("command", choices=("plan", "apply"))
    parser.add_argument("--env", default="test")
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        settings = SettingsLoader.load(args.env)
        manifest = _load_manifest(args.manifest)
        client = DatabaseClient.from_settings(replace(settings.database, read_only=True))
        tool = PersistentDatasetSeedTool(client, environment=settings.environment)
        plan = tool.plan(manifest)
        if args.command == "plan":
            print(json.dumps(plan.as_dict(), ensure_ascii=False, indent=2, default=str))
            return 0
        tool.apply(manifest)
    except SeedContractBlocked as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"SEED_PLAN_INVALID: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

