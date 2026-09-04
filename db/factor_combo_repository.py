"""组合因子台测试所需的 MySQL 数据访问与测试数据状态准备。"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import combinations
from typing import Any
from uuid import uuid4

from db.client import DatabaseClient, DatabaseTransaction
from db.factor_combo_cleanup import (
    FactorComboCleanupMixin,
    _TEST_PARENT_FACTOR_PREFIX,
)


_TEST_PARENT_EXCLUDED_COLUMNS = {
    "id",
    "created_at",
    "updated_at",
    "latest_status_updated_at",
}

_MAX_PARENT_EXPANDED_SUB_FACTORS = 12


@dataclass(frozen=True)
class SubFactorChoice:
    """表示可用于组合因子测试的一个真实子因子。

    参数包含子因子主键、唯一名称及一个可用的母因子主键和名称。
    返回值由 ``FactorComboRepository`` 的因子选择方法产生，供表单和 Worker 组件构造使用。
    """

    sub_factor_id: int
    sub_factor_name: str
    parent_factor_id: int
    parent_factor_name: str


@dataclass(frozen=True)
class ParentFactorChoice:
    """表示至少关联多个子因子的真实母因子。

    参数包含母因子主键、唯一名称以及已排序的关联子因子。
    返回值由 ``find_parent_with_sub_factors`` 产生，供验证母因子展开规则使用。
    """

    factor_id: int
    factor_name: str
    sub_factors: tuple[SubFactorChoice, ...]


@dataclass(frozen=True)
class RegisteredFactorChoice:
    """表示可用于核心指标与公式回归的真实已登记复合子因子。

    参数包含登记、组合版本、复合子因子、因子详情和登记初始有效性快照的主键，以及接口回查所需的子因子名称。
    返回值由 ``find_registered_factor_with_refresh_evidence`` 动态查询，不绑定固定测试数据。
    """

    registration_id: int
    sub_factor_id: int
    parent_factor_id: int
    version_id: int
    factor_detail_id: int
    registration_validity_status_id: int
    sub_factor_name: str
    combo_version_hash: str


@dataclass(frozen=True)
class DetachedPoolMember:
    """保存被测试临时移出因子池的成员快照。"""

    row: dict[str, Any]


class FactorComboRepository(FactorComboCleanupMixin):
    """封装组合因子测试的 MySQL 查询、受控状态准备和数据清理。"""

    def __init__(self, client: DatabaseClient, environment: str) -> None:
        """初始化组合因子数据仓储。

        参数 ``client`` 是已配置 MySQL 的 ``DatabaseClient``，``environment`` 是当前自动化环境名称。
        不返回值；所有写操作在执行前都会校验环境必须为 ``test``。
        """

        self._client = client
        self._environment = environment.strip().lower()
        self._test_parent_factor_ids_by_form: dict[int, set[int]] = {}
        self._test_parent_relation_pairs_by_owner: dict[int, set[tuple[int, int]]] = {}

    def find_parent_with_sub_factors(
        self,
        minimum_sub_factors: int = 2,
        maximum_sub_factors: int = _MAX_PARENT_EXPANDED_SUB_FACTORS,
    ) -> ParentFactorChoice | None:
        """查找一个拥有足够关联子因子的真实母因子及其全部关联子因子。

        参数 ``minimum_sub_factors`` 是所需最少关联子因子数，``maximum_sub_factors`` 是允许展开的最多子因子数。
        查询不依赖有效性评分，也不截断超过上限的母因子；返回按子因子 ID 升序排列的完整
        ``ParentFactorChoice``。测试库中不存在符合条件的母因子时返回 ``None``。
        """

        if minimum_sub_factors < 1 or maximum_sub_factors < minimum_sub_factors:
            raise ValueError("maximum_sub_factors must be >= minimum_sub_factors >= 1")
        rows = self._client.fetch_all(
            """
            SELECT DISTINCT
                f.id AS factor_id,
                f.factor_name,
                r.sub_factor_id,
                sf.sub_factor_name
            FROM factors AS f
            INNER JOIN factor_sub_factor_relations AS r
                ON r.factor_id = f.id
            INNER JOIN sub_factors AS sf
                ON sf.id = r.sub_factor_id
            WHERE f.factor_name IS NOT NULL
              AND TRIM(f.factor_name) <> ''
              AND sf.sub_factor_name IS NOT NULL
              AND TRIM(sf.sub_factor_name) <> ''
            ORDER BY f.id ASC, r.sub_factor_id ASC
            """
        )
        grouped: dict[int, list[SubFactorChoice]] = {}
        factor_names: dict[int, str] = {}
        for row in rows:
            factor_id = int(row["factor_id"])
            factor_names[factor_id] = str(row["factor_name"])
            grouped.setdefault(factor_id, []).append(
                SubFactorChoice(
                    sub_factor_id=int(row["sub_factor_id"]),
                    sub_factor_name=str(row["sub_factor_name"]),
                    parent_factor_id=factor_id,
                    parent_factor_name=str(row["factor_name"]),
                )
            )
        for factor_id, sub_factors in grouped.items():
            if minimum_sub_factors <= len(sub_factors) <= maximum_sub_factors:
                return ParentFactorChoice(
                    factor_id=factor_id,
                    factor_name=factor_names[factor_id],
                    sub_factors=tuple(sub_factors),
                )
        return None

    def find_parent_choices(
        self,
        minimum_sub_factors: int = 2,
        minimum_parent_count: int = 2,
        maximum_sub_factors: int = _MAX_PARENT_EXPANDED_SUB_FACTORS,
        maximum_expanded_sub_factors: int = _MAX_PARENT_EXPANDED_SUB_FACTORS,
    ) -> tuple[ParentFactorChoice, ...] | None:
        """查找多个满足展开条件的真实母因子及其完整子因子集合。

        参数 ``minimum_sub_factors`` 是每个母因子至少需要的子因子数量，``minimum_parent_count`` 是需要返回的母因子
        数量，``maximum_sub_factors`` 是单个母因子的展开上限，``maximum_expanded_sub_factors`` 是多个母因子去重
        后的展开总上限。返回按母因子 ID 排序的不可变选择集合；测试库当前不存在满足组合条件的母因子时返回 ``None``，
        不会截断或伪造因子关系。
        """

        if minimum_sub_factors < 1 or minimum_parent_count < 1:
            raise ValueError("minimum_sub_factors and minimum_parent_count must be positive")
        if maximum_sub_factors < minimum_sub_factors:
            raise ValueError("maximum_sub_factors must be >= minimum_sub_factors")
        if maximum_expanded_sub_factors < minimum_sub_factors:
            raise ValueError("maximum_expanded_sub_factors must be >= minimum_sub_factors")
        rows = self._client.fetch_all(
            """
            SELECT DISTINCT
                f.id AS factor_id,
                f.factor_name,
                r.sub_factor_id,
                sf.sub_factor_name
            FROM factors AS f
            INNER JOIN factor_sub_factor_relations AS r
                ON r.factor_id = f.id
            INNER JOIN sub_factors AS sf
                ON sf.id = r.sub_factor_id
            WHERE f.factor_name IS NOT NULL
              AND TRIM(f.factor_name) <> ''
              AND sf.sub_factor_name IS NOT NULL
              AND TRIM(sf.sub_factor_name) <> ''
            ORDER BY f.id ASC, r.sub_factor_id ASC
            """
        )
        grouped: dict[int, list[SubFactorChoice]] = {}
        factor_names: dict[int, str] = {}
        for row in rows:
            factor_id = int(row["factor_id"])
            factor_names[factor_id] = str(row["factor_name"])
            grouped.setdefault(factor_id, []).append(
                SubFactorChoice(
                    sub_factor_id=int(row["sub_factor_id"]),
                    sub_factor_name=str(row["sub_factor_name"]),
                    parent_factor_id=factor_id,
                    parent_factor_name=str(row["factor_name"]),
                )
            )
        eligible = [
            ParentFactorChoice(
                factor_id=factor_id,
                factor_name=factor_names[factor_id],
                sub_factors=tuple(sub_factors),
            )
            for factor_id, sub_factors in grouped.items()
            if minimum_sub_factors <= len(sub_factors) <= maximum_sub_factors
        ]
        for candidate in combinations(eligible, minimum_parent_count):
            expanded_ids = {
                sub_factor.sub_factor_id
                for parent in candidate
                for sub_factor in parent.sub_factors
            }
            if len(expanded_ids) <= maximum_expanded_sub_factors:
                return tuple(candidate)
        return None

    def find_sub_factor_choices(self, minimum_count: int = 2) -> tuple[SubFactorChoice, ...]:
        """读取可用于测试数据准备的去重子因子集合。

        参数 ``minimum_count`` 是调用方需要的最少子因子数量。返回按子因子 ID 排序且每个 ID 只出现一次的
        ``SubFactorChoice`` 元组；可用数量不足时抛出 ``RuntimeError``，不返回不完整的前置数据。
        """

        if minimum_count < 1:
            raise ValueError("minimum_count must be positive")
        rows = self._client.fetch_all(
            """
            SELECT
                relation.factor_id AS parent_factor_id,
                parent.factor_name AS parent_factor_name,
                sub_factor.id AS sub_factor_id,
                sub_factor.sub_factor_name
            FROM factor_sub_factor_relations AS relation
            INNER JOIN factors AS parent
                ON parent.id = relation.factor_id
            INNER JOIN sub_factors AS sub_factor
                ON sub_factor.id = relation.sub_factor_id
            WHERE parent.factor_name IS NOT NULL
              AND TRIM(parent.factor_name) <> ''
              AND sub_factor.sub_factor_name IS NOT NULL
              AND TRIM(sub_factor.sub_factor_name) <> ''
            ORDER BY sub_factor.id ASC, relation.factor_id ASC
            """
        )
        choices: list[SubFactorChoice] = []
        seen_ids: set[int] = set()
        for row in rows:
            choice = self._to_sub_factor_choice(row)
            if choice is None or choice.sub_factor_id in seen_ids:
                continue
            seen_ids.add(choice.sub_factor_id)
            choices.append(choice)
        if len(choices) < minimum_count:
            raise RuntimeError(
                f"Test database has fewer than {minimum_count} distinct usable sub-factors"
            )
        return tuple(choices)

    def ensure_parent_choices_for_test(
        self,
        owner_id: int,
        minimum_sub_factors: int = 2,
        minimum_parent_count: int = 2,
    ) -> tuple[ParentFactorChoice, ...]:
        """确保测试场景拥有足够的可展开母因子，并登记临时资源归属。

        参数 ``owner_id`` 是当前测试 Scope 的会话或表单 ID，用于失败清理时定位临时资源；``minimum_sub_factors``
        是每个母因子需要关联的最少子因子数量；``minimum_parent_count`` 是需要的母因子数量。返回满足条件的真实或
        测试母因子选择集合；数据库没有足够真实子因子、写入失败或写入后无法回读时抛出 ``RuntimeError``，不会静默
        降级为跳过。
        """

        if minimum_sub_factors < 1 or minimum_parent_count < 1:
            raise ValueError("minimum_sub_factors and minimum_parent_count must be positive")
        existing = self.find_parent_choices(minimum_sub_factors, minimum_parent_count)
        if existing is not None:
            return existing

        source_choices = self.find_sub_factor_choices(minimum_sub_factors)
        # 单个已有母因子可能与其他母因子的展开集合超过总上限，因此不能按“已有数量”简单补差。
        # 没有满足完整组合时创建所需数量的临时母因子，回查时再按去重总量重新选择。
        create_count = minimum_parent_count

        self._assert_test_write_allowed()
        relation_pairs: set[tuple[int, int]] = set()
        with self._client.transaction() as transaction:
            for index in range(create_count):
                parent_id = self._create_test_parent_factor(
                    transaction,
                    owner_id,
                    purpose="multiple-parent-expansion",
                    ordinal=index,
                )
                for choice in source_choices[:minimum_sub_factors]:
                    result = transaction.execute(
                        """
                        INSERT INTO factor_sub_factor_relations (factor_id, sub_factor_id)
                        VALUES (%s, %s)
                        """,
                        (parent_id, choice.sub_factor_id),
                    )
                    if result.rowcount != 1:
                        raise RuntimeError(
                            "Temporary parent-to-sub-factor relation could not be created: "
                            f"parent={parent_id}, sub_factor={choice.sub_factor_id}"
                        )
                    relation_pairs.add((parent_id, choice.sub_factor_id))

        self._test_parent_factor_ids_by_form.setdefault(int(owner_id), set()).update(
            parent_id for parent_id, _ in relation_pairs
        )
        self._test_parent_relation_pairs_by_owner.setdefault(int(owner_id), set()).update(relation_pairs)
        prepared = self.find_parent_choices(minimum_sub_factors, minimum_parent_count)
        if prepared is None:
            raise RuntimeError("Temporary parent factors were written but cannot be read back")
        return prepared

    def find_sub_factor_pair(self) -> tuple[SubFactorChoice, SubFactorChoice] | None:
        """查找两个可用于表单提交的真实子因子。

        不接收参数。
        返回优先来自不同母因子的两个 ``SubFactorChoice``；测试库中不足两个可用子因子时返回 ``None``。
        """

        rows = self._client.fetch_all(
            """
            SELECT
                r.factor_id AS parent_factor_id,
                f.factor_name AS parent_factor_name,
                r.sub_factor_id,
                sf.sub_factor_name
            FROM factor_sub_factor_relations AS r
            INNER JOIN factors AS f
                ON f.id = r.factor_id
            INNER JOIN sub_factors AS sf
                ON sf.id = r.sub_factor_id
            WHERE f.factor_name IS NOT NULL
              AND TRIM(f.factor_name) <> ''
              AND sf.sub_factor_name IS NOT NULL
              AND TRIM(sf.sub_factor_name) <> ''
            ORDER BY r.factor_id ASC, r.sub_factor_id ASC
            """
        )
        choices: list[SubFactorChoice] = []
        seen_sub_factor_ids: set[int] = set()
        for row in rows:
            sub_factor_id = int(row["sub_factor_id"])
            if sub_factor_id in seen_sub_factor_ids:
                continue
            seen_sub_factor_ids.add(sub_factor_id)
            choices.append(
                SubFactorChoice(
                    sub_factor_id=sub_factor_id,
                    sub_factor_name=str(row["sub_factor_name"]),
                    parent_factor_id=int(row["parent_factor_id"]),
                    parent_factor_name=str(row["parent_factor_name"]),
                )
            )
        for first_index, first_choice in enumerate(choices):
            for second_choice in choices[first_index + 1 :]:
                if second_choice.parent_factor_id != first_choice.parent_factor_id:
                    return first_choice, second_choice
        if len(choices) >= 2:
            return choices[0], choices[1]
        return None

    def find_sub_factor_outside_pool(self, pool_id: int) -> SubFactorChoice | None:
        """查找一个不属于指定锁定池的真实子因子。

        参数 ``pool_id`` 是因子池主键。
        返回一个 ``SubFactorChoice``；没有可用池外子因子时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT
                r.factor_id AS parent_factor_id,
                f.factor_name AS parent_factor_name,
                sf.id AS sub_factor_id,
                sf.sub_factor_name
            FROM sub_factors AS sf
            INNER JOIN factor_sub_factor_relations AS r
                ON r.sub_factor_id = sf.id
            INNER JOIN factors AS f
                ON f.id = r.factor_id
            WHERE NOT EXISTS (
                SELECT 1
                FROM factor_combo_pool_member AS member
                WHERE member.pool_id = %s
                  AND member.sub_factor_id = sf.id
            )
            ORDER BY sf.id ASC
            LIMIT 1
            """,
            (pool_id,),
        )
        return self._to_sub_factor_choice(row)

    def find_unrelated_parent_factor(self, sub_factor_id: int, excluded_factor_id: int) -> int | None:
        """查找一个与指定子因子没有父子关联的真实母因子。

        参数 ``sub_factor_id`` 是目标子因子主键，``excluded_factor_id`` 是已知合法父因子主键。
        返回不关联该子因子的母因子主键；没有符合条件的母因子时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT f.id AS factor_id
            FROM factors AS f
            WHERE f.id <> %s
              AND LEFT(f.factor_name, CHAR_LENGTH(%s)) <> %s
              AND NOT EXISTS (
                SELECT 1
                FROM factor_sub_factor_relations AS relation_item
                WHERE relation_item.factor_id = f.id
                  AND relation_item.sub_factor_id = %s
              )
            ORDER BY f.id ASC
            LIMIT 1
            """,
            (excluded_factor_id, _TEST_PARENT_FACTOR_PREFIX, _TEST_PARENT_FACTOR_PREFIX, sub_factor_id),
        )
        return int(row["factor_id"]) if row is not None else None

    @contextmanager
    def temporarily_detach_pool_member(
        self,
        form_id: int,
        sub_factor_id: int,
    ) -> Iterator[DetachedPoolMember]:
        """在一次接口调用期间临时移出测试表单的一个因子池成员。

        参数 ``form_id`` 是当前测试创建的组合表单主键，``sub_factor_id`` 是该表单池中的子因子主键。
        返回上下文管理器，内容是被移出的成员快照；离开上下文时无论接口调用成功或抛出异常都会恢复原成员。
        仅允许测试环境写入；成员不存在、池归属不一致、快照字段缺失或恢复失败时抛出 ``RuntimeError``，不会静默跳过。
        """

        self._assert_test_write_allowed()
        normalized_form_id = int(form_id)
        normalized_sub_factor_id = int(sub_factor_id)
        with self._client.transaction() as transaction:
            row = transaction.fetch_one(
                """
                SELECT
                    member.*,
                    form.factor_combo_pool_id AS form_pool_id
                FROM factor_combo_pool_member AS member
                INNER JOIN factor_combo_form AS form
                    ON form.id = member.factor_combo_form_id
                WHERE member.factor_combo_form_id = %s
                  AND member.sub_factor_id = %s
                FOR UPDATE
                """,
                (normalized_form_id, normalized_sub_factor_id),
            )
            if row is None:
                raise RuntimeError(
                    f"Pool member does not belong to test form: form={normalized_form_id}, "
                    f"sub_factor={normalized_sub_factor_id}"
                )
            if row.get("form_pool_id") is None or int(row["pool_id"]) != int(row["form_pool_id"]):
                raise RuntimeError(
                    f"Pool member pool does not match test form: form={normalized_form_id}, "
                    f"sub_factor={normalized_sub_factor_id}"
                )
            if row.get("id") is None:
                raise RuntimeError("Pool member snapshot is missing its primary key")
            required_columns = (
                "factor_combo_form_id",
                "pool_id",
                "sub_factor_id",
                "factor_detail_id",
                "created_by",
                "updated_by",
            )
            missing_columns = [column for column in required_columns if column not in row]
            if missing_columns:
                raise RuntimeError(f"Pool member snapshot is missing columns: {missing_columns}")
            result = transaction.execute(
                "DELETE FROM factor_combo_pool_member WHERE id = %s",
                (int(row["id"]),),
            )
            if result.rowcount != 1:
                raise RuntimeError(f"Pool member could not be detached: {row['id']}")
            snapshot = DetachedPoolMember(row=dict(row))
        try:
            yield snapshot
        finally:
            self._restore_detached_pool_member(snapshot)

    def ensure_unrelated_parent_factor_for_test(
        self,
        form_id: int,
        sub_factor_id: int,
        excluded_factor_id: int,
    ) -> int:
        """取得或创建一个与指定子因子无关系的测试母因子。

        参数 ``form_id`` 是将使用该母因子的测试表单主键，``sub_factor_id`` 是目标池内子因子主键，
        ``excluded_factor_id`` 是当前已知父因子主键。优先返回数据库中已有的不相关母因子；找不到时仅在测试环境
        创建带 ``__questtest_unrelated_parent__`` 标记的临时母因子，并登记到表单资源图中，返回母因子主键。
        数据库写入失败或无法确认新记录主键时抛出 ``RuntimeError``，不返回 ``None``。
        """

        existing_factor_id = self.find_unrelated_parent_factor(sub_factor_id, excluded_factor_id)
        if existing_factor_id is not None:
            return existing_factor_id

        self._assert_test_write_allowed()
        token = uuid4().hex
        factor_name = f"{_TEST_PARENT_FACTOR_PREFIX}{token}"
        serial_number = f"questtest-parent-{token}"
        metadata = json.dumps(
            {"questtest": True, "purpose": "unrelated-parent-factor", "form_id": int(form_id)},
            separators=(",", ":"),
        )
        with self._client.transaction() as transaction:
            factor_id = self._create_test_parent_factor(
                transaction,
                form_id,
                purpose="unrelated-parent-factor",
                excluded_factor_id=excluded_factor_id,
            )
        normalized_factor_id = int(factor_id)
        self._test_parent_factor_ids_by_form.setdefault(int(form_id), set()).add(normalized_factor_id)
        return normalized_factor_id

    def _create_test_parent_factor(
        self,
        transaction: DatabaseTransaction,
        owner_id: int,
        *,
        purpose: str,
        ordinal: int = 0,
        excluded_factor_id: int | None = None,
    ) -> int:
        """在当前事务中克隆一个带测试标记的母因子。

        参数 ``transaction`` 是测试数据库事务，``owner_id`` 是资源归属 ID，``purpose`` 是准备场景标识，``ordinal``
        用于同一场景内生成多个稳定可区分的标记，``excluded_factor_id`` 是可选的来源排除 ID。返回新母因子主键；
        来源记录、Schema、插入结果或新 ID 无法确认时抛出 ``RuntimeError``。
        """

        source_parameters: tuple[Any, ...]
        if excluded_factor_id is None:
            source_query = """
                SELECT *
                FROM factors
                WHERE LEFT(factor_name, CHAR_LENGTH(%s)) <> %s
                ORDER BY id ASC
                LIMIT 1
            """
            source_parameters = (_TEST_PARENT_FACTOR_PREFIX, _TEST_PARENT_FACTOR_PREFIX)
        else:
            source_query = """
                SELECT *
                FROM factors
                WHERE id <> %s
                  AND LEFT(factor_name, CHAR_LENGTH(%s)) <> %s
                ORDER BY id ASC
                LIMIT 1
            """
            source_parameters = (
                int(excluded_factor_id),
                _TEST_PARENT_FACTOR_PREFIX,
                _TEST_PARENT_FACTOR_PREFIX,
            )
        source = transaction.fetch_one(source_query, source_parameters)
        if source is None and excluded_factor_id is not None:
            source = transaction.fetch_one(
                """
                SELECT *
                FROM factors
                WHERE LEFT(factor_name, CHAR_LENGTH(%s)) <> %s
                ORDER BY id ASC
                LIMIT 1
                """,
                (_TEST_PARENT_FACTOR_PREFIX, _TEST_PARENT_FACTOR_PREFIX),
            )
        if source is None:
            raise RuntimeError("Cannot create a test parent factor because factors has no source row")

        token = uuid4().hex
        factor_name = f"{_TEST_PARENT_FACTOR_PREFIX}{token}"
        serial_number = f"questtest-parent-{token}-{ordinal}"
        metadata = json.dumps(
            {
                "questtest": True,
                "purpose": purpose,
                "owner_id": int(owner_id),
                "ordinal": int(ordinal),
            },
            separators=(",", ":"),
        )
        insert_columns = self._factor_insert_columns(transaction, source)
        required_columns = {"factor_name", "cn_name", "serial_number"}
        missing_columns = sorted(required_columns - set(insert_columns))
        if missing_columns:
            raise RuntimeError(f"Factors schema is missing required columns: {missing_columns}")
        values_by_column: dict[str, Any] = {}
        for column in insert_columns:
            value = source.get(column)
            if column in {"factor_name", "cn_name"}:
                value = factor_name
            elif column == "serial_number":
                value = serial_number
            elif column == "serial_prefix":
                value = "questtest"
            elif column in {"parent_factor_id", "parent_factor", "parent_factor_name"}:
                value = None
            elif column == "factor_theme":
                value = "__questtest__"
            elif column == "metadata":
                value = metadata
            elif column == "level":
                value = 1
            elif column == "child_factor_count":
                value = 0
            elif column == "max_level":
                value = 1
            elif isinstance(value, (dict, list)):
                value = json.dumps(value, separators=(",", ":"))
            values_by_column[column] = value
        columns = tuple(values_by_column)
        result = transaction.execute(
            f"INSERT INTO factors ({', '.join(columns)}) VALUES ({self._placeholders(columns)})",
            tuple(values_by_column[column] for column in columns),
        )
        factor_id = result.lastrowid
        if factor_id is None:
            row = transaction.fetch_one(
                "SELECT id FROM factors WHERE factor_name = %s",
                (factor_name,),
            )
            factor_id = row.get("id") if row is not None else None
        if factor_id is None:
            raise RuntimeError(f"Created test parent factor cannot be located: {factor_name}")
        return int(factor_id)

    @staticmethod
    def _factor_insert_columns(
        transaction: DatabaseTransaction,
        source: Mapping[str, Any],
    ) -> tuple[str, ...]:
        """读取 ``factors`` 的可插入字段，避免临时数据依赖固定版本的列清单。

        参数 ``transaction`` 是当前测试事务，``source`` 是用于克隆的母因子记录。
        返回经过数据库字段元数据和标识符校验的列名；自增列、时间自动生成列和生成列会被排除。
        字段元数据缺失或返回非法列名时抛出 ``RuntimeError``，不执行不确定的动态 SQL。
        """

        metadata_rows = transaction.fetch_all("SHOW COLUMNS FROM factors")
        if not metadata_rows:
            raise RuntimeError("Factors schema metadata is empty")
        source_columns = set(source)
        columns: list[str] = []
        for metadata in metadata_rows:
            column = metadata.get("Field") or metadata.get("field")
            if not isinstance(column, str) or not column:
                raise RuntimeError(f"Factors schema metadata has no valid column name: {metadata}")
            if not (column[0].isalpha() or column[0] == "_") or not all(
                character.isalnum() or character == "_" for character in column
            ):
                raise RuntimeError(f"Factors schema metadata has an unsafe column name: {column!r}")
            extra = str(metadata.get("Extra") or metadata.get("extra") or "").lower()
            if column in _TEST_PARENT_EXCLUDED_COLUMNS:
                continue
            if "auto_increment" in extra or "generated" in extra:
                continue
            if column in source_columns:
                columns.append(column)
        if not columns:
            raise RuntimeError("Factors schema has no insertable columns shared with the source row")
        return tuple(dict.fromkeys(columns))

    def _restore_detached_pool_member(self, snapshot: DetachedPoolMember) -> None:
        """恢复一次测试临时移出的因子池成员。"""

        row = snapshot.row
        required_columns = (
            "factor_combo_form_id",
            "pool_id",
            "sub_factor_id",
            "factor_detail_id",
            "created_by",
            "updated_by",
        )
        missing_columns = [column for column in required_columns if column not in row]
        if missing_columns:
            raise RuntimeError(f"Pool member restore is missing columns: {missing_columns}")
        columns = (
            "factor_combo_form_id",
            "pool_id",
            "sub_factor_id",
            "factor_detail_id",
            "metrics_snapshot_json",
            "validity_snapshot_json",
            "created_by",
            "created_at",
            "updated_by",
            "updated_at",
            "definition_snapshot_json",
            "sort_order",
        )
        available_columns = tuple(column for column in columns if column in row)
        placeholders = self._placeholders(available_columns)
        values: list[Any] = []
        for column in available_columns:
            value = row[column]
            if column.endswith("_json") and isinstance(value, (dict, list)):
                value = json.dumps(value, separators=(",", ":"))
            values.append(value)

        self._assert_test_write_allowed()
        with self._client.transaction() as transaction:
            existing = transaction.fetch_one(
                """
                SELECT id, factor_combo_form_id
                FROM factor_combo_pool_member
                WHERE pool_id = %s
                  AND sub_factor_id = %s
                LIMIT 1
                """,
                (int(row["pool_id"]), int(row["sub_factor_id"])),
            )
            if existing is not None:
                if int(existing["factor_combo_form_id"]) != int(row["factor_combo_form_id"]):
                    raise RuntimeError(
                        "Detached pool member was re-created under a different form: "
                        f"sub_factor={row['sub_factor_id']}"
                    )
                update_columns = tuple(column for column in available_columns if column != "id")
                if update_columns:
                    assignments = ", ".join(f"{column} = %s" for column in update_columns)
                    update_values = tuple(
                        values[available_columns.index(column)] for column in update_columns
                    )
                    transaction.execute(
                        f"""
                        UPDATE factor_combo_pool_member
                        SET {assignments}
                        WHERE id = %s
                        """,
                        update_values + (int(existing["id"]),),
                    )
                return
            result = transaction.execute(
                f"""
                INSERT INTO factor_combo_pool_member ({', '.join(available_columns)})
                VALUES ({placeholders})
                """,
                tuple(values),
            )
            if result.rowcount != 1:
                raise RuntimeError(f"Detached pool member could not be restored: {row['sub_factor_id']}")

    def get_form(self, form_id: int) -> dict[str, Any] | None:
        """读取组合研究表单及其关联指针。

        参数 ``form_id`` 是 ``factor_combo_form`` 主键。
        返回标准化后的表单字典；记录不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT form.*
            FROM factor_combo_form AS form
            WHERE form.id = %s
            """,
            (form_id,),
        )
        return self._normalize_database_row(row)

    def get_work_order_data_spec(self, form_id: int) -> dict[str, Any] | None:
        """读取表单中明确持久化的 Work Order ``data_spec`` 快照。

        参数 ``form_id`` 是 ``factor_combo_form`` 主键。返回数据库明确保存的 ``data_spec`` 对象；优先读取表单
        记录中的专用 ``data_spec``/``data_spec_json``/``work_order_json`` 字段，其次读取 ``form_json.data_spec``。
        如果数据库只保存了请求配置而没有保存 Work Order 快照则返回 ``None``，调用方必须将其报告为契约缺口，
        不在 Repository 中根据默认值猜测接口返回内容。
        """

        form = self.get_form(form_id)
        if form is None:
            return None
        for field_name in ("data_spec", "data_spec_json", "work_order_json"):
            value = form.get(field_name)
            if isinstance(value, Mapping):
                return dict(value)
            if isinstance(value, str) and value.strip():
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError as error:
                    raise RuntimeError(f"Persisted work order {field_name} is invalid JSON") from error
                if not isinstance(parsed, Mapping):
                    raise RuntimeError(f"Persisted work order {field_name} must be a JSON object")
                return dict(parsed)
        form_json = form.get("form_json")
        if isinstance(form_json, Mapping) and isinstance(form_json.get("data_spec"), Mapping):
            return dict(form_json["data_spec"])
        return None

    def count_forms_for_session(self, session_id: int) -> int:
        """统计指定会话下的组合研究表单数量。

        参数 ``session_id`` 是 Chat Session 主键。
        返回表单记录数量；没有记录时返回零。
        """

        row = self._client.fetch_one(
            "SELECT COUNT(*) AS record_count FROM factor_combo_form WHERE session_id = %s",
            (session_id,),
        )
        return int(row["record_count"]) if row is not None else 0

    def get_chat_session(self, session_id: int) -> dict[str, Any] | None:
        """读取组合表单关联的 Chat Session 稳定字段。

        参数 ``session_id`` 是 ``chat_sessions.id`` 主键。
        返回会话 ID、会话键、用户、标题和状态；会话不存在时返回 ``None``。
        方法只读数据库，不修改会话或其消息。
        """

        row = self._client.fetch_one(
            """
            SELECT id, session_key, user_id, title, status
            FROM chat_sessions
            WHERE id = %s
            """,
            (session_id,),
        )
        return self._normalize_database_row(row)

    def count_pipeline_runs_for_session_key(self, session_key: str) -> int:
        """统计指定 Agent/Pipeline 会话键下的运行数量。

        参数 ``session_key`` 是 ``pipeline_runs.session_key`` 的完整值。
        返回匹配运行记录数；没有运行时返回零。方法只读数据库，用于确认表单提交阶段没有提前创建 Pipeline Run。
        """

        normalized_session_key = str(session_key).strip()
        if not normalized_session_key:
            raise ValueError("session_key must not be blank")
        row = self._client.fetch_one(
            "SELECT COUNT(*) AS record_count FROM pipeline_runs WHERE session_key = %s",
            (normalized_session_key,),
        )
        return int(row["record_count"]) if row is not None else 0

    def get_pool(self, pool_id: int) -> dict[str, Any] | None:
        """读取一个组合因子池的状态和快照。

        参数 ``pool_id`` 是 ``factor_combo_pool.pool_id``。
        返回标准化后的因子池字典；记录不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT pool.*
            FROM factor_combo_pool AS pool
            WHERE pool.pool_id = %s
            """,
            (pool_id,),
        )
        return self._normalize_database_row(row)

    def get_pool_members(self, form_id: int) -> list[dict[str, Any]]:
        """读取一个表单锁定池的全部成员及对应母因子。

        参数 ``form_id`` 是组合研究表单主键。
        返回按 ``sort_order`` 排序的成员字典列表；表单没有因子池成员时返回空列表。
        """

        rows = self._client.fetch_all(
            """
            SELECT
                member.*,
                member.id AS member_id,
                member.factor_combo_form_id AS member_form_id,
                member.pool_id AS member_pool_id,
                member.factor_detail_id AS member_factor_detail_id,
                member.sort_order AS member_sort_order,
                member.definition_snapshot_json AS member_definition_snapshot_json,
                member.metrics_snapshot_json AS member_metrics_snapshot_json,
                member.validity_snapshot_json AS member_validity_snapshot_json,
                sf.sub_factor_name,
                sf.cn_name AS sub_factor_cn_name,
                sf.serial_number AS sub_factor_serial_number,
                sf.factor_bar_interval AS sub_factor_bar_interval,
                detail.id AS factor_detail_record_id,
                detail.factor_id AS factor_detail_factor_id,
                detail.is_sub_factor_id AS factor_detail_is_sub_factor_id,
                detail.name AS factor_detail_name,
                detail.serial_number AS factor_detail_serial_number,
                detail.status AS factor_detail_status,
                detail.updated_at AS factor_detail_updated_at,
                (
                    SELECT MIN(relation_item.factor_id)
                    FROM factor_sub_factor_relations AS relation_item
                    WHERE relation_item.sub_factor_id = member.sub_factor_id
                ) AS parent_factor_id,
                (
                    SELECT GROUP_CONCAT(
                        DISTINCT relation_item.factor_id
                        ORDER BY relation_item.factor_id ASC
                        SEPARATOR ','
                    )
                    FROM factor_sub_factor_relations AS relation_item
                    WHERE relation_item.sub_factor_id = member.sub_factor_id
                ) AS parent_factor_ids,
                (
                    SELECT GROUP_CONCAT(
                        parent_item.factor_name
                        ORDER BY relation_item.factor_id ASC
                        SEPARATOR ','
                    )
                    FROM factor_sub_factor_relations AS relation_item
                    INNER JOIN factors AS parent_item
                        ON parent_item.id = relation_item.factor_id
                    WHERE relation_item.sub_factor_id = member.sub_factor_id
                ) AS parent_factor_names,
                (
                    SELECT GROUP_CONCAT(
                        parent_item.serial_number
                        ORDER BY relation_item.factor_id ASC
                        SEPARATOR ','
                    )
                    FROM factor_sub_factor_relations AS relation_item
                    INNER JOIN factors AS parent_item
                        ON parent_item.id = relation_item.factor_id
                    WHERE relation_item.sub_factor_id = member.sub_factor_id
                ) AS parent_factor_serial_numbers,
                (
                    SELECT COUNT(*)
                    FROM factor_sub_factor_relations AS relation_item
                    WHERE relation_item.sub_factor_id = member.sub_factor_id
                ) AS parent_factor_relation_count,
                (
                    SELECT COUNT(DISTINCT relation_item.factor_id)
                    FROM factor_sub_factor_relations AS relation_item
                    WHERE relation_item.sub_factor_id = member.sub_factor_id
                ) AS parent_factor_distinct_count,
                parent.factor_name AS parent_factor_name,
                parent.cn_name AS parent_factor_cn_name,
                parent.serial_number AS parent_factor_serial_number
            FROM factor_combo_pool_member AS member
            INNER JOIN sub_factors AS sf
                ON sf.id = member.sub_factor_id
            LEFT JOIN factors_details AS detail
                ON detail.id = member.factor_detail_id
            LEFT JOIN factors AS parent
                ON parent.id = (
                    SELECT MIN(relation_item.factor_id)
                    FROM factor_sub_factor_relations AS relation_item
                    WHERE relation_item.sub_factor_id = member.sub_factor_id
                )
            WHERE member.factor_combo_form_id = %s
            ORDER BY member.sort_order ASC, member.id ASC
            """,
            (form_id,),
        )
        normalized_rows: list[dict[str, Any]] = []
        for row in rows:
            normalized = self._normalize_database_row(row) or {}
            parent_ids = normalized.get("parent_factor_ids")
            if isinstance(parent_ids, str):
                normalized["parent_factor_ids"] = [
                    int(value)
                    for value in parent_ids.split(",")
                    if value.strip().isdigit()
                ]
            for source_name, target_name in (
                ("parent_factor_names", "parent_factor_names"),
                ("parent_factor_serial_numbers", "parent_factor_serial_numbers"),
            ):
                value = normalized.get(source_name)
                if isinstance(value, str):
                    normalized[target_name] = [item for item in value.split(",") if item.strip()]
            # 这些 canonical 别名保留独立名称，避免上层把 member.id、detail.id 或版本业务 ID 混为一谈。
            normalized.setdefault("factor_combo_form_id", normalized.get("member_form_id"))
            normalized.setdefault("pool_id", normalized.get("member_pool_id"))
            normalized.setdefault("factor_detail_id", normalized.get("member_factor_detail_id"))
            normalized_rows.append(normalized)
        return normalized_rows

    def get_combo_version(self, version_id: int) -> dict[str, Any] | None:
        """读取一个具体组合版本。

        参数 ``version_id`` 是 ``factor_combo.id``，不是业务级 ``combo_id``。
        返回组合版本字典；记录不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT version.*
            FROM factor_combo AS version
            WHERE version.id = %s
            """,
            (version_id,),
        )
        return self._normalize_database_row(row)

    def count_versions_for_form(self, form_id: int) -> int:
        """统计指定表单产生的组合版本数量。

        参数 ``form_id`` 是组合研究表单主键。
        返回版本数量；没有记录时返回零。
        """

        row = self._client.fetch_one(
            """
            SELECT COUNT(*) AS record_count
            FROM factor_combo
            WHERE initial_form_id = %s
               OR combo_family_key = CONCAT('factor-combo-form:', %s)
            """,
            (form_id, form_id),
        )
        return int(row["record_count"]) if row is not None else 0

    def count_experiments_for_form(self, form_id: int) -> int:
        """统计指定表单及其组合版本关联的实验数量。

        参数 ``form_id`` 是组合研究表单主键。
        返回通过表单指针或版本初始表单关联的去重实验数量；没有实验时返回零。方法只读数据库。
        """

        normalized_form_id = int(form_id)
        row = self._client.fetch_one(
            """
            SELECT COUNT(DISTINCT experiment.id) AS record_count
            FROM factor_combo_experiment_info AS experiment
            INNER JOIN factor_combo AS version
                ON version.experiment_id = experiment.id
                OR version.best_experiment_result_id = experiment.id
            LEFT JOIN factor_combo_form AS form
                ON form.id = %s
            WHERE (
                form.factor_combo_experiment_info_id = experiment.id
                OR version.initial_form_id = %s
                OR version.combo_family_key = CONCAT('factor-combo-form:', %s)
            )
            """,
            (normalized_form_id, normalized_form_id, normalized_form_id),
        )
        return int(row["record_count"]) if row is not None else 0

    def clear_experiment_links_for_test(self, form_id: int, version_id: int) -> None:
        """模拟实验写入中间阶段失败，清除表单和版本的实验指针。

        参数 ``form_id`` 是自动化创建的组合表单主键，``version_id`` 是其具体 ``factor_combo.id`` 主键。
        不返回值；方法仅允许测试环境写入，并保留实验主体记录，供后续相同请求验证幂等恢复。
        """

        self._assert_test_write_allowed()
        with self._client.transaction() as transaction:
            form_result = transaction.execute(
                """
                UPDATE factor_combo_form
                SET status = %s,
                    factor_combo_experiment_info_id = NULL
                WHERE id = %s
                """,
                ("processing", int(form_id)),
            )
            version_result = transaction.execute(
                """
                UPDATE factor_combo
                SET experiment_id = NULL
                WHERE id = %s
                """,
                (int(version_id),),
            )
        if form_result.rowcount != 1 or version_result.rowcount != 1:
            raise RuntimeError(
                f"Cannot clear experiment links for test form/version: {form_id}/{version_id}"
            )

    def clear_next_version_links_for_test(self, feedback_id: int, form_id: int) -> None:
        """模拟下一版本已创建但 Feedback 和表单指针尚未完成的中间状态。

        参数 ``feedback_id`` 是自动化创建的 Feedback 主键，``form_id`` 是其所属组合表单主键。
        不返回值；方法仅允许测试环境写入，保留下一版本及组件记录，供相同请求验证接口的幂等补写能力。
        """

        self._assert_test_write_allowed()
        with self._client.transaction() as transaction:
            feedback_result = transaction.execute(
                """
                UPDATE factor_combo_experiment_feedback
                SET next_pipeline_run_id = NULL,
                    next_factor_combo_version_id = NULL,
                    next_experiment_info_id = NULL,
                    status = %s,
                    completed_at = NULL,
                    processing_error = NULL
                WHERE id = %s
                """,
                ("processing", int(feedback_id)),
            )
            form_result = transaction.execute(
                """
                UPDATE factor_combo_form
                SET status = %s,
                    pipeline_run_id = NULL,
                    factor_combo_id = NULL,
                    factor_combo_experiment_info_id = NULL
                WHERE id = %s
                """,
                ("processing", int(form_id)),
            )
        if feedback_result.rowcount != 1 or form_result.rowcount != 1:
            raise RuntimeError(
                f"Cannot clear next-version links for test feedback/form: {feedback_id}/{form_id}"
            )

    def clear_next_version_pointer_for_test(
        self,
        feedback_id: int,
        form_id: int,
        pointer_name: str,
    ) -> None:
        """只清除下一版本流程中的一个关联指针以模拟单步写入中断。

        参数 ``feedback_id`` 和 ``form_id`` 必须属于同一条测试 Feedback 链路，``pointer_name`` 只能是
        ``feedback_next_pipeline_run_id``、``feedback_next_factor_combo_version_id``、``form_pipeline_run_id`` 或
        ``form_factor_combo_id``。不返回值；方法仅允许测试环境写入，参数非法、记录不存在或归属不匹配时抛出
        ``ValueError`` 或 ``RuntimeError``，不会执行宽泛更新。
        """

        pointer_columns = {
            "feedback_next_pipeline_run_id": ("factor_combo_experiment_feedback", "next_pipeline_run_id"),
            "feedback_next_factor_combo_version_id": (
                "factor_combo_experiment_feedback",
                "next_factor_combo_version_id",
            ),
            "form_pipeline_run_id": ("factor_combo_form", "pipeline_run_id"),
            "form_factor_combo_id": ("factor_combo_form", "factor_combo_id"),
        }
        target = pointer_columns.get(pointer_name)
        if target is None:
            raise ValueError(f"Unsupported next-version pointer: {pointer_name}")

        self._assert_test_write_allowed()
        with self._client.transaction() as transaction:
            feedback = transaction.fetch_one(
                """
                SELECT id, form_id
                FROM factor_combo_experiment_feedback
                WHERE id = %s
                FOR UPDATE
                """,
                (int(feedback_id),),
            )
            if feedback is None:
                raise RuntimeError(f"Factor combo feedback does not exist: {feedback_id}")
            if int(feedback["form_id"]) != int(form_id):
                raise RuntimeError(
                    f"Feedback/form ownership mismatch for pointer test: {feedback_id}/{form_id}"
                )
            if target[0] == "factor_combo_experiment_feedback":
                result = transaction.execute(
                    f"UPDATE factor_combo_experiment_feedback SET {target[1]} = NULL WHERE id = %s",
                    (int(feedback_id),),
                )
            else:
                result = transaction.execute(
                    f"UPDATE factor_combo_form SET {target[1]} = NULL WHERE id = %s",
                    (int(form_id),),
                )
        if result.rowcount != 1:
            raise RuntimeError(
                f"Next-version pointer was not cleared for test: {pointer_name}"
            )

    def prepare_feedback_partial_replay_for_test(
        self,
        feedback_id: int,
        form_id: int,
        version_id: int,
        experiment_info_id: int,
        pipeline_run_id: str,
    ) -> None:
        """把已完成的 Feedback 链路还原为文档定义的 ``preparing`` 半成品状态。

        参数分别是 Feedback、表单、组合版本、实验记录主键和表单原始 Pipeline ID。方法仅允许测试环境写入，并先
        校验五个实体属于同一条链路；随后恢复 Feedback 插入后、后续拒绝/清空步骤尚未完成的状态。无返回值，归属
        校验失败、记录缺失或最终快照不符合预期时抛出 ``RuntimeError``。
        """

        self._assert_test_write_allowed()
        with self._client.transaction() as transaction:
            feedback = transaction.fetch_one(
                """
                SELECT id, form_id, source_factor_combo_version_id, source_experiment_info_id
                FROM factor_combo_experiment_feedback
                WHERE id = %s
                FOR UPDATE
                """,
                (int(feedback_id),),
            )
            form = transaction.fetch_one(
                """
                SELECT id, factor_combo_id, factor_combo_experiment_info_id
                FROM factor_combo_form
                WHERE id = %s
                FOR UPDATE
                """,
                (int(form_id),),
            )
            version = transaction.fetch_one(
                "SELECT id, combo_id FROM factor_combo WHERE id = %s FOR UPDATE",
                (int(version_id),),
            )
            experiment = transaction.fetch_one(
                "SELECT id, combo_id FROM factor_combo_experiment_info WHERE id = %s FOR UPDATE",
                (int(experiment_info_id),),
            )
            if any(item is None for item in (feedback, form, version, experiment)):
                raise RuntimeError(
                    "Feedback partial replay setup is missing one of its linked records"
                )
            if feedback is None or form is None or version is None or experiment is None:
                raise RuntimeError("Feedback partial replay setup records became unavailable")
            if int(feedback["form_id"]) != int(form_id):
                raise RuntimeError("Feedback does not belong to the requested form")
            if int(feedback["source_factor_combo_version_id"]) != int(version_id):
                raise RuntimeError("Feedback source version does not match the requested version")
            if int(feedback["source_experiment_info_id"]) != int(experiment_info_id):
                raise RuntimeError("Feedback source experiment does not match the requested experiment")
            if int(experiment["combo_id"]) != int(version["combo_id"]):
                raise RuntimeError("Experiment and source version have different combo identities")

            transaction.execute(
                """
                UPDATE factor_combo_experiment_feedback
                SET status = %s,
                    claimed_at = NULL,
                    next_pipeline_run_id = NULL,
                    next_factor_combo_version_id = NULL,
                    next_experiment_info_id = NULL,
                    processing_error = NULL,
                    completed_at = NULL
                WHERE id = %s
                """,
                ("preparing", int(feedback_id)),
            )
            transaction.execute(
                "UPDATE factor_combo SET status = %s WHERE id = %s",
                ("candidate", int(version_id)),
            )
            transaction.execute(
                """
                UPDATE factor_combo_experiment_info
                SET valid = %s,
                    failure_reason = NULL
                WHERE id = %s
                """,
                (True, int(experiment_info_id)),
            )
            transaction.execute(
                """
                UPDATE factor_combo_form
                SET status = %s,
                    pipeline_run_id = %s,
                    factor_combo_id = %s,
                    factor_combo_experiment_info_id = %s
                WHERE id = %s
                """,
                ("completed", str(pipeline_run_id), int(version_id), int(experiment_info_id), int(form_id)),
            )

        snapshot = self.get_feedback(feedback_id), self.get_form(form_id), self.get_combo_version(version_id), self.get_experiment(experiment_info_id)
        if any(item is None for item in snapshot):
            raise RuntimeError("Feedback partial replay setup could not be verified")
        feedback_row, form_row, version_row, experiment_row = snapshot
        if feedback_row["status"] != "preparing" or form_row["status"] != "completed":
            raise RuntimeError("Feedback partial replay setup has an unexpected final state")
        if version_row["status"] != "candidate" or not bool(experiment_row["valid"]):
            raise RuntimeError("Feedback partial replay setup did not restore the source state")

    def clear_registration_marker_for_test(self, version_id: int) -> None:
        """删除指定组合版本的最终登记标记以模拟登记最后一步写入失败。

        参数 ``version_id`` 是测试创建的具体 ``factor_combo.id``，不是业务级 ``combo_id``。方法仅允许测试环境写入，
        会先按具体版本和版本哈希锁定唯一登记记录，再删除该记录；不存在、匹配多条或删除行数异常时抛出
        ``RuntimeError``，不会删除其他组合版本的登记数据。
        """

        self._assert_test_write_allowed()
        with self._client.transaction() as transaction:
            rows = transaction.fetch_all(
                """
                SELECT registered.id
                FROM factor_combo_registered_factor AS registered
                INNER JOIN factor_combo AS version
                    ON registered.combo_id = version.combo_id
                   AND registered.combo_version_hash = version.combo_version_hash
                WHERE version.id = %s
                FOR UPDATE
                """,
                (int(version_id),),
            )
            if len(rows) != 1:
                raise RuntimeError(
                    f"Expected exactly one registration marker for version {version_id}, found {len(rows)}"
                )
            result = transaction.execute(
                "DELETE FROM factor_combo_registered_factor WHERE id = %s",
                (int(rows[0]["id"]),),
            )
        if result.rowcount != 1:
            raise RuntimeError(f"Registration marker was not cleared for version {version_id}")

    def clear_one_next_version_component_for_test(self, version_id: int) -> int:
        """删除指定下一版本的一条组件记录以模拟组件阶段写入中断。

        参数 ``version_id`` 是测试创建的具体 ``factor_combo.id``。返回被删除的组件主键；方法仅允许测试环境写入，
        只在该版本恰好有可识别组件时删除一条，并在删除后验证仍存在目标版本，避免影响其他版本或误删全部组件。
        记录不存在或删除行数异常时抛出 ``RuntimeError``。
        """

        self._assert_test_write_allowed()
        with self._client.transaction() as transaction:
            row = transaction.fetch_one(
                """
                SELECT component.id
                FROM factor_combo_component AS component
                INNER JOIN factor_combo AS version
                    ON version.id = component.combo_id
                WHERE version.id = %s
                ORDER BY component.id ASC
                LIMIT 1
                FOR UPDATE
                """,
                (int(version_id),),
            )
            if row is None:
                raise RuntimeError(f"No component exists for next-version test: {version_id}")
            result = transaction.execute(
                "DELETE FROM factor_combo_component WHERE id = %s",
                (int(row["id"]),),
            )
            if result.rowcount != 1:
                raise RuntimeError(f"Next-version component was not cleared: {version_id}")
            version = transaction.fetch_one(
                "SELECT id FROM factor_combo WHERE id = %s",
                (int(version_id),),
            )
            if version is None:
                raise RuntimeError(f"Target version disappeared after component clear: {version_id}")
        return int(row["id"])

    def count_components(self, version_id: int) -> int:
        """统计具体组合版本的组件数量。

        参数 ``version_id`` 是具体 ``factor_combo.id``。
        返回组件数量；没有记录时返回零。
        """

        row = self._client.fetch_one(
            "SELECT COUNT(*) AS record_count FROM factor_combo_component WHERE combo_id = %s",
            (version_id,),
        )
        return int(row["record_count"]) if row is not None else 0

    def get_components(self, version_id: int) -> list[dict[str, Any]]:
        """读取一个组合版本的全部组件。

        参数 ``version_id`` 是具体 ``factor_combo.id``。
        返回组件字典列表；未写入组件时返回空列表。
        """

        rows = self._client.fetch_all(
            """
            SELECT
                component.*,
                factor_item.factor_name,
                factor_item.cn_name AS factor_cn_name,
                sub_factor_item.sub_factor_name,
                sub_factor_item.cn_name AS sub_factor_cn_name,
                sub_factor_item.serial_number AS sub_factor_serial_number
            FROM factor_combo_component AS component
            INNER JOIN factors AS factor_item
                ON factor_item.id = component.component_factor_id
            INNER JOIN sub_factors AS sub_factor_item
                ON sub_factor_item.id = component.component_sub_factor_id
            WHERE component.combo_id = %s
            ORDER BY
                component.component_sub_factor_id ASC,
                component.component_factor_id ASC,
                component.id ASC
            """,
            (version_id,),
        )
        return [self._normalize_database_row(row) or {} for row in rows]

    def get_experiment(self, experiment_info_id: int) -> dict[str, Any] | None:
        """读取一个组合因子实验信息记录。

        参数 ``experiment_info_id`` 是 ``factor_combo_experiment_info.id``。
        返回实验字典；记录不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT experiment.*
            FROM factor_combo_experiment_info AS experiment
            WHERE experiment.id = %s
            """,
            (experiment_info_id,),
        )
        return self._normalize_database_row(row)

    def get_experiment_by_external_id(self, experiment_id: str) -> dict[str, Any] | None:
        """按接口幂等键读取组合实验记录。

        参数 ``experiment_id`` 是实验写入接口路径中的业务标识。
        返回实验字典；不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT experiment.*
            FROM factor_combo_experiment_info AS experiment
            WHERE experiment.experiment_id = %s
            """,
            (experiment_id,),
        )
        return self._normalize_database_row(row)

    def count_experiments_by_artifact_uri(self, artifact_uri: str) -> int:
        """统计指定 Artifact URI 关联的实验记录数。

        参数 ``artifact_uri`` 是实验产物 URI。
        返回匹配的实验记录数量；没有记录时返回零。
        """

        row = self._client.fetch_one(
            "SELECT COUNT(*) AS record_count FROM factor_combo_experiment_info WHERE artifact_uri = %s",
            (artifact_uri,),
        )
        return int(row["record_count"]) if row is not None else 0

    def count_experiments_by_artifact_hash(self, artifact_sha256: str) -> int:
        """统计指定 Artifact SHA256 对应的实验记录数。

        参数 ``artifact_sha256`` 是十六进制内容摘要。
        返回匹配的实验记录数量；没有记录时返回零。
        """

        row = self._client.fetch_one(
            "SELECT COUNT(*) AS record_count FROM factor_combo_experiment_info WHERE artifact_hash = %s",
            (artifact_sha256.lower(),),
        )
        return int(row["record_count"]) if row is not None else 0

    def find_existing_local_artifact(self) -> dict[str, Any] | None:
        """查找一个已有的绝对路径 Artifact，供 SHA256 冲突场景复用其可读内容。

        不接收参数。返回包含 ``artifact_uri`` 和 ``artifact_hash`` 的已有实验字典；测试库没有绝对路径 Artifact
        时返回 ``None``。该方法只读数据库，不验证文件本身，文件可读性由实验接口在真实请求中验证。
        """

        row = self._client.fetch_one(
            """
            SELECT artifact_uri, artifact_hash
            FROM factor_combo_experiment_info
            WHERE artifact_uri LIKE '/%'
              AND artifact_hash IS NOT NULL
            ORDER BY id DESC
            LIMIT 1
            """
        )
        return self._normalize_database_row(row)

    def get_feedback(self, feedback_id: int) -> dict[str, Any] | None:
        """读取组合报告反馈及下一轮关联指针。

        参数 ``feedback_id`` 是 ``factor_combo_experiment_feedback`` 主键。
        返回反馈字典；记录不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT feedback.*
            FROM factor_combo_experiment_feedback AS feedback
            WHERE feedback.id = %s
            """,
            (feedback_id,),
        )
        return self._normalize_database_row(row)

    def count_feedback_for_form(self, form_id: int) -> int:
        """统计一个表单的反馈记录数量。

        参数 ``form_id`` 是组合研究表单主键。
        返回反馈记录数量；没有记录时返回零。
        """

        row = self._client.fetch_one(
            "SELECT COUNT(*) AS record_count FROM factor_combo_experiment_feedback WHERE form_id = %s",
            (form_id,),
        )
        return int(row["record_count"]) if row is not None else 0

    def get_feedback_for_form(self, form_id: int) -> list[dict[str, Any]]:
        """读取一个表单的全部 Feedback 记录，供并发和状态流转对账使用。

        参数 ``form_id`` 是组合研究表单主键。
        返回按主键升序排列的 Feedback 字典列表；没有记录时返回空列表。
        """

        rows = self._client.fetch_all(
            """
            SELECT feedback.*
            FROM factor_combo_experiment_feedback AS feedback
            WHERE feedback.form_id = %s
            ORDER BY feedback.id ASC
            """,
            (form_id,),
        )
        return [self._normalize_database_row(row) or {} for row in rows]

    def get_registration(
        self,
        combo_id: int,
        version_id: int | None = None,
        combo_version_hash: str | None = None,
    ) -> dict[str, Any] | None:
        """按组合业务标识或具体版本读取唯一登记完成标记。

        参数 ``combo_id`` 是组合业务标识，``version_id`` 是具体 ``factor_combo.id``，``combo_version_hash`` 是版本内容
        哈希。登记表的 ``combo_id`` 按新版契约关联 ``factor_combo.combo_id``，再以版本哈希定位具体版本；可选版本主键
        用于进一步限定目标。查询最多读取两条结果；若身份条件仍命中多条登记记录则抛出 ``RuntimeError``，不会静默
        取最新一条。
        返回带有 ``version_id``、``version_business_id`` 和 ``version_combo_version_hash`` canonical 字段的登记字典；
        未找到时返回 ``None``。
        """

        predicates = [
            "version.combo_id = %s",
            "registered.combo_id = version.combo_id",
            "registered.combo_version_hash = version.combo_version_hash",
        ]
        parameters: list[Any] = [int(combo_id)]
        if version_id is not None:
            predicates.append("version.id = %s")
            parameters.append(int(version_id))
        if combo_version_hash is not None:
            predicates.append("registered.combo_version_hash = %s")
            parameters.append(str(combo_version_hash).strip().lower())
        rows = self._client.fetch_all(
            f"""
            SELECT
                registered.*,
                version.id AS version_id,
                version.combo_id AS version_business_id,
                version.combo_version_hash AS version_combo_version_hash
            FROM factor_combo_registered_factor AS registered
            INNER JOIN factor_combo AS version
                ON {' AND '.join(predicates)}
            ORDER BY registered.id ASC
            LIMIT 2
            """,
            tuple(parameters),
        )
        if len(rows) > 1:
            raise RuntimeError(
                "Multiple factor combo registrations match the same concrete version identity: "
                f"combo_id={combo_id}, version_id={version_id}, combo_version_hash={combo_version_hash!r}"
            )
        return self._normalize_database_row(rows[0] if rows else None)

    def find_registered_factor_with_refresh_evidence(self) -> RegisteredFactorChoice | None:
        """查找具备完整真实刷新证据和来源关系的已登记复合子因子。

        不接收参数。返回最新的 ``RegisteredFactorChoice``；候选必须同时具备时序/截面核心汇总、截面回测字段、
        两类原始切片、刷新有效性快照、组合组件和至少一条来源关系。测试库没有符合条件的数据时返回 ``None``。
        方法只读当前数据库，不创建、修改或清理业务数据，也不会把登记占位快照当成刷新结果。
        """

        rows = self._client.fetch_all(
            """
            SELECT
                registered.id AS registration_id,
                registered.sub_factor_id,
                registered.factor_id AS parent_factor_id,
                registered.combo_version_hash,
                version.id AS version_id,
                sub_factor.sub_factor_name,
                (
                    SELECT detail.id
                    FROM factors_details AS detail
                    WHERE detail.factor_id = registered.sub_factor_id
                      AND detail.is_sub_factor_id = 1
                    ORDER BY detail.updated_at DESC, detail.id DESC
                    LIMIT 1
                ) AS factor_detail_id,
                (
                    SELECT validity.id
                    FROM factor_validity_status AS validity
                    WHERE validity.factor_id = registered.sub_factor_id
                      AND validity.is_sub_factor_id = 1
                      AND validity.run_id = CONCAT(
                          'factor_combo_register:',
                          registered.combo_version_hash
                      )
                    ORDER BY validity.id ASC
                    LIMIT 1
                ) AS registration_validity_status_id
            FROM factor_combo_registered_factor AS registered
            INNER JOIN factor_combo AS version
                ON version.combo_id = registered.combo_id
               AND version.combo_version_hash = registered.combo_version_hash
            INNER JOIN sub_factors AS sub_factor
                ON sub_factor.id = registered.sub_factor_id
            WHERE EXISTS (
                SELECT 1
                FROM factor_ic_summary_metrics AS summary
                WHERE summary.factor_id = registered.sub_factor_id
                  AND summary.is_sub_factor_id = 1
                  AND summary.ic_scope = 'time_series'
                  AND summary.symbol = ''
                  AND summary.mean_ic IS NOT NULL
                  AND summary.icir IS NOT NULL
                  AND summary.mean_rank_ic IS NOT NULL
                  AND summary.rank_icir IS NOT NULL
            )
              AND EXISTS (
                SELECT 1
                FROM factor_ic_summary_metrics AS summary
                WHERE summary.factor_id = registered.sub_factor_id
                  AND summary.is_sub_factor_id = 1
                  AND summary.ic_scope = 'cross_sectional'
                  AND summary.symbol = ''
                  AND summary.mean_ic IS NOT NULL
                  AND summary.icir IS NOT NULL
                  AND summary.mean_rank_ic IS NOT NULL
                  AND summary.rank_icir IS NOT NULL
                  AND (
                      summary.ic_t_stat IS NOT NULL
                      OR summary.rank_ic_t_stat IS NOT NULL
                  )
                  AND summary.monotonicity_ratio IS NOT NULL
                  AND (
                      summary.mean_long_short_return IS NOT NULL
                      OR summary.long_short_annual_return IS NOT NULL
                      OR summary.long_short_t_stat IS NOT NULL
                  )
            )
              AND EXISTS (
                SELECT 1
                FROM factor_validity_status AS validity
                WHERE validity.factor_id = registered.sub_factor_id
                  AND validity.is_sub_factor_id = 1
                  AND (
                      validity.time_series_summary_id IS NOT NULL
                      OR validity.cross_sectional_summary_id IS NOT NULL
                  )
            )
              AND EXISTS (
                SELECT 1
                FROM factor_ic_slice_metrics AS slice_metric
                WHERE slice_metric.factor_id = registered.sub_factor_id
                  AND slice_metric.is_sub_factor_id = 1
                  AND slice_metric.ic_scope = 'time_series'
            )
              AND EXISTS (
                SELECT 1
                FROM factor_ic_slice_metrics AS slice_metric
                WHERE slice_metric.factor_id = registered.sub_factor_id
                  AND slice_metric.is_sub_factor_id = 1
                  AND slice_metric.ic_scope = 'cross_sectional'
            )
              AND EXISTS (
                SELECT 1
                FROM factor_combo_component AS component
                WHERE component.combo_id = version.id
            )
              AND (
                EXISTS (
                    SELECT 1
                    FROM factor_sub_factor_relations AS relation
                    WHERE relation.sub_factor_id = registered.sub_factor_id
                      AND relation.factor_id <> registered.sub_factor_id
                )
                OR EXISTS (
                    SELECT 1
                    FROM sub_factor_parent_relations AS relation
                    WHERE relation.sub_factor_id = registered.sub_factor_id
                      AND relation.parent_sub_factor_id <> registered.sub_factor_id
                )
            )
            ORDER BY registered.id DESC
            LIMIT 1
            """
        )
        if not rows:
            return None
        row = rows[0]
        required_positive_ids = {
            "registration_id": row.get("registration_id"),
            "sub_factor_id": row.get("sub_factor_id"),
            "parent_factor_id": row.get("parent_factor_id"),
            "version_id": row.get("version_id"),
            "factor_detail_id": row.get("factor_detail_id"),
            "registration_validity_status_id": row.get("registration_validity_status_id"),
        }
        normalized_ids: dict[str, int] = {}
        for field_name, value in required_positive_ids.items():
            if value is None or isinstance(value, bool):
                raise RuntimeError(f"Registered factor refresh candidate is missing {field_name}")
            try:
                normalized = int(value)
            except (TypeError, ValueError, OverflowError) as error:
                raise RuntimeError(
                    f"Registered factor refresh candidate has invalid {field_name}"
                ) from error
            if normalized <= 0:
                raise RuntimeError(f"Registered factor refresh candidate has invalid {field_name}")
            normalized_ids[field_name] = normalized
        sub_factor_name = str(row.get("sub_factor_name") or "").strip()
        combo_version_hash = str(row.get("combo_version_hash") or "").strip().lower()
        if not sub_factor_name:
            raise RuntimeError("Registered factor refresh candidate is missing sub_factor_name")
        if len(combo_version_hash) != 64:
            raise RuntimeError("Registered factor refresh candidate has invalid combo_version_hash")
        return RegisteredFactorChoice(
            registration_id=normalized_ids["registration_id"],
            sub_factor_id=normalized_ids["sub_factor_id"],
            parent_factor_id=normalized_ids["parent_factor_id"],
            version_id=normalized_ids["version_id"],
            factor_detail_id=normalized_ids["factor_detail_id"],
            registration_validity_status_id=normalized_ids["registration_validity_status_id"],
            sub_factor_name=sub_factor_name,
            combo_version_hash=combo_version_hash,
        )

    def count_registrations_for_version(self, version_id: int) -> int:
        """统计指定具体组合版本对应的登记标记数量。

        参数 ``version_id`` 是 ``factor_combo.id``，不是业务级 ``combo_id``。返回通过业务组合 ID 和版本哈希同时
        匹配的登记记录数；没有登记时返回零。方法只读数据库，专门用于并发幂等场景，不会因出现重复记录而静默取一条。
        """

        row = self._client.fetch_one(
            """
            SELECT COUNT(*) AS record_count
            FROM factor_combo_registered_factor AS registered
            INNER JOIN factor_combo AS version
                ON registered.combo_id = version.combo_id
               AND registered.combo_version_hash = version.combo_version_hash
            WHERE version.id = %s
            """,
            (int(version_id),),
        )
        return int(row["record_count"]) if row is not None else 0

    def get_registered_sub_factor(self, sub_factor_id: int) -> dict[str, Any] | None:
        """读取登记接口创建的复合子因子。

        参数 ``sub_factor_id`` 是登记响应中的子因子主键。
        返回子因子核心字段；记录不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT sub_factor.*
            FROM sub_factors AS sub_factor
            WHERE sub_factor.id = %s
            """,
            (sub_factor_id,),
        )
        return self._normalize_database_row(row)

    def get_registered_factor_detail(self, factor_detail_id: int) -> dict[str, Any] | None:
        """读取登记接口创建的因子详情。

        参数 ``factor_detail_id`` 是登记响应中的详情主键。
        返回因子详情核心字段；记录不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT detail.*
            FROM factors_details AS detail
            WHERE detail.id = %s
            """,
            (factor_detail_id,),
        )
        return self._normalize_database_row(row)

    def get_registered_validity_status(self, validity_status_id: int) -> dict[str, Any] | None:
        """读取登记接口创建的有效性快照。

        参数 ``validity_status_id`` 是登记响应中的有效性记录主键。
        返回有效性和审计字段；记录不存在时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT validity.*
            FROM factor_validity_status AS validity
            WHERE validity.id = %s
            """,
            (validity_status_id,),
        )
        return self._normalize_database_row(row)

    def get_registered_source_relations(self, sub_factor_id: int) -> dict[str, Any]:
        """读取登记复合子因子的版本组件和完整来源关系图。

        参数 ``sub_factor_id`` 是登记响应返回的复合子因子主键。返回值包含具体组合版本、组件、直接母因子关系
        和子因子谱系关系；登记记录不存在时返回空的关系集合。该方法只查询数据库，不根据名称或更新时间猜测来源，
        也不会把 ``factors_details.factor_id`` 当成母因子关系。
        """

        registration_rows = self._client.fetch_all(
            """
            SELECT
                registered.*,
                version.id AS version_id,
                version.combo_id AS version_business_combo_id,
                version.combo_version_hash AS version_combo_version_hash
            FROM factor_combo_registered_factor AS registered
            INNER JOIN factor_combo AS version
                ON version.combo_id = registered.combo_id
               AND version.combo_version_hash = registered.combo_version_hash
            WHERE registered.sub_factor_id = %s
            ORDER BY registered.id ASC
            LIMIT 2
            """,
            (int(sub_factor_id),),
        )
        if len(registration_rows) > 1:
            raise RuntimeError(
                "Multiple registration rows match one registered sub-factor: "
                f"sub_factor_id={sub_factor_id}"
            )
        registration = self._normalize_database_row(registration_rows[0]) if registration_rows else None
        if registration is None:
            return {
                "registration": None,
                "version": None,
                "components": [],
                "parent_factor_relations": [],
                "parent_sub_factor_relations": [],
            }

        version_id = int(registration["version_id"])
        parent_factor_rows = self._client.fetch_all(
            """
            SELECT
                relation.id,
                relation.factor_id,
                relation.sub_factor_id,
                parent.factor_name,
                parent.cn_name,
                parent.serial_number
            FROM factor_sub_factor_relations AS relation
            INNER JOIN factors AS parent
                ON parent.id = relation.factor_id
            WHERE relation.sub_factor_id = %s
              AND relation.factor_id <> %s
            ORDER BY relation.factor_id ASC, relation.id ASC
            """,
            (int(sub_factor_id), int(sub_factor_id)),
        )
        parent_sub_factor_rows = self._client.fetch_all(
            """
            SELECT
                relation.id,
                relation.parent_sub_factor_id,
                relation.sub_factor_id,
                parent.sub_factor_name AS parent_sub_factor_name,
                parent.cn_name AS parent_sub_factor_cn_name,
                child.sub_factor_name,
                child.cn_name
            FROM sub_factor_parent_relations AS relation
            INNER JOIN sub_factors AS parent
                ON parent.id = relation.parent_sub_factor_id
            INNER JOIN sub_factors AS child
                ON child.id = relation.sub_factor_id
            WHERE relation.sub_factor_id = %s
              AND relation.parent_sub_factor_id <> %s
            ORDER BY relation.parent_sub_factor_id ASC, relation.id ASC
            """,
            (int(sub_factor_id), int(sub_factor_id)),
        )
        version = self.get_combo_version(version_id)
        return {
            "registration": registration,
            "version": version,
            "components": self.get_components(version_id),
            "parent_factor_relations": [
                self._normalize_database_row(row) or {} for row in parent_factor_rows
            ],
            "parent_sub_factor_relations": [
                self._normalize_database_row(row) or {} for row in parent_sub_factor_rows
            ],
        }

    def get_factor_refresh_calculation_runs(self, sub_factor_id: int) -> list[dict[str, Any]]:
        """读取指定复合子因子在新版指标汇总表中的计算 Run 证据。

        参数 ``sub_factor_id`` 是登记接口创建的复合子因子主键。
        返回按更新时间倒序聚合的 ``factor_ic_summary_metrics`` 记录；每行包含计算 Run 状态、汇总行数和至少一个
        非空 IC/ICIR/t-stat/有效性评分指标的行数。该方法只读权威新版指标表 ``factor_ic_summary_metrics`` 及其 Run 主表
        ``factor_ic_runs``，不会读取已废弃的 ``factor_mining_symbol_window_metric``，数据库异常直接向调用方抛出。
        """

        return self._client.fetch_all(
            """
            SELECT
                summary.factor_id,
                summary.is_sub_factor_id,
                summary.run_id,
                runs.status AS run_status,
                COUNT(*) AS summary_row_count,
                SUM(
                    CASE
                        WHEN summary.coverage_mean IS NOT NULL
                          OR summary.coverage_min IS NOT NULL
                          OR summary.mean_ic IS NOT NULL
                          OR summary.median_ic IS NOT NULL
                          OR summary.std_ic IS NOT NULL
                          OR summary.icir IS NOT NULL
                          OR summary.mean_abs_ic IS NOT NULL
                          OR summary.positive_ic_rate IS NOT NULL
                          OR summary.mean_rank_ic IS NOT NULL
                          OR summary.median_rank_ic IS NOT NULL
                          OR summary.std_rank_ic IS NOT NULL
                          OR summary.rank_icir IS NOT NULL
                          OR summary.mean_abs_rank_ic IS NOT NULL
                          OR summary.positive_rank_ic_rate IS NOT NULL
                          OR summary.ic_t_stat IS NOT NULL
                          OR summary.rank_ic_t_stat IS NOT NULL
                          OR summary.monotonicity_ratio IS NOT NULL
                          OR summary.mean_long_short_return IS NOT NULL
                          OR summary.long_short_annual_return IS NOT NULL
                          OR summary.long_short_t_stat IS NOT NULL
                          OR summary.is_icir IS NOT NULL
                          OR summary.oos_icir IS NOT NULL
                          OR summary.icir_oos_retention IS NOT NULL
                          OR summary.rank_is_icir IS NOT NULL
                          OR summary.rank_oos_icir IS NOT NULL
                          OR summary.rank_icir_oos_retention IS NOT NULL
                          OR summary.slice_count IS NOT NULL
                          OR summary.valid_slice_count IS NOT NULL
                          OR summary.is_period_start IS NOT NULL
                          OR summary.is_period_end IS NOT NULL
                          OR summary.oos_period_start IS NOT NULL
                          OR summary.oos_period_end IS NOT NULL
                          OR summary.is_slice_count IS NOT NULL
                          OR summary.oos_slice_count IS NOT NULL
                          OR summary.ic_score IS NOT NULL
                          OR summary.rank_ic_score IS NOT NULL
                          OR summary.icir_score IS NOT NULL
                          OR summary.rank_icir_score IS NOT NULL
                          OR summary.t_stat_score IS NOT NULL
                          OR summary.oos_retention_score IS NOT NULL
                          OR summary.monotonicity_score IS NOT NULL
                          OR summary.long_short_score IS NOT NULL
                          OR summary.final_score IS NOT NULL
                          OR summary.mean_stratification IS NOT NULL
                        THEN 1
                        ELSE 0
                    END
                ) AS populated_metric_row_count,
                COUNT(DISTINCT summary.ic_scope) AS ic_scope_count,
                MAX(summary.updated_at) AS latest_updated_at
            FROM factor_ic_summary_metrics AS summary
            LEFT JOIN factor_ic_runs AS runs
                ON runs.run_id = summary.run_id
            WHERE summary.factor_id = %s
              AND summary.is_sub_factor_id = 1
            GROUP BY
                summary.factor_id,
                summary.is_sub_factor_id,
                summary.run_id,
                runs.status
            ORDER BY latest_updated_at DESC, summary.run_id DESC
            """,
            (sub_factor_id,),
        )

    def get_factor_refresh_calculation_metrics(self, sub_factor_id: int) -> list[dict[str, Any]]:
        """读取指定复合子因子的新版 IC 汇总明细及其计算 Run 状态。

        参数 ``sub_factor_id`` 是登记接口创建的复合子因子主键。
        返回 ``factor_ic_summary_metrics`` 的明细行，包含完整汇总字段、``summary_id`` 别名和关联的
        ``factor_ic_runs.status``；调用方可据此按有效性快照引用的 Run/summary 精确筛选本次刷新结果。
        方法只读新版指标表和 Run 主表，不读取已废弃的 ``factor_mining_symbol_window_metric``。
        """

        rows = self._client.fetch_all(
            """
            SELECT
                summary.*,
                summary.id AS summary_id,
                runs.status AS run_status,
                runs.id AS run_record_id,
                runs.run_name AS run_name,
                runs.window_config_version AS run_window_config_version,
                runs.scoring_version AS run_scoring_version,
                runs.interval_value AS run_interval_value,
                runs.forward_return_horizon AS run_forward_return_horizon,
                runs.universe_key AS run_universe_key,
                runs.weighting_method AS run_weighting_method,
                runs.weight_lookback_days AS run_weight_lookback_days,
                runs.method AS run_method,
                runs.data_start AS run_data_start,
                runs.data_end AS run_data_end,
                runs.config_hash AS run_config_hash,
                runs.config_json AS run_config_json,
                runs.created_at AS run_created_at
            FROM factor_ic_summary_metrics AS summary
            LEFT JOIN factor_ic_runs AS runs
                ON runs.run_id = summary.run_id
            WHERE summary.factor_id = %s
              AND summary.is_sub_factor_id = 1
            ORDER BY summary.updated_at DESC, summary.id DESC
            """,
            (sub_factor_id,),
        )
        return [self._normalize_database_row(row) or {} for row in rows]

    def get_factor_refresh_calculation_slices(
        self,
        sub_factor_id: int,
        run_ids: Sequence[str],
    ) -> list[dict[str, Any]]:
        """读取本次刷新 Run 对应的原始 IC/回测切片指标。

        参数 ``sub_factor_id`` 是登记生成的复合子因子主键，``run_ids`` 是刷新响应或有效性快照明确关联的计算
        Run ID 集合。返回新版 ``factor_ic_slice_metrics`` 明细；Run 集合为空时返回空列表。查询只使用参数化条件，
        不读取已废弃的挖掘指标表，也不修改数据库。
        """

        normalized_run_ids = tuple(dict.fromkeys(str(run_id).strip() for run_id in run_ids if str(run_id).strip()))
        if not normalized_run_ids:
            return []
        placeholders = ", ".join(["%s"] * len(normalized_run_ids))
        rows = self._client.fetch_all(
            f"""
            SELECT slice_metric.*
            FROM factor_ic_slice_metrics AS slice_metric
            WHERE slice_metric.factor_id = %s
              AND slice_metric.is_sub_factor_id = 1
              AND slice_metric.run_id IN ({placeholders})
            ORDER BY slice_metric.run_id ASC,
                     slice_metric.ic_scope ASC,
                     slice_metric.window_scope ASC,
                     slice_metric.slice_start ASC,
                     slice_metric.id ASC
            """,
            (int(sub_factor_id), *normalized_run_ids),
        )
        return [self._normalize_database_row(row) or {} for row in rows]

    def get_factor_refresh_run_details(self, sub_factor_id: int) -> list[dict[str, Any]]:
        """读取目标子因子所有新版指标 Run 的完整主表记录。

        参数 ``sub_factor_id`` 是登记后生成的复合子因子主键。
        返回按创建时间倒序排列的 ``factor_ic_runs`` 完整记录列表；只返回至少有一条汇总指标的 Run，数据库查询或
        JSON 解析异常直接向调用方抛出。该方法用于核对 Run 的配置身份、状态和汇总记录归属，不读取旧版指标表。
        """

        rows = self._client.fetch_all(
            """
            SELECT DISTINCT runs.*
            FROM factor_ic_runs AS runs
            INNER JOIN factor_ic_summary_metrics AS summary
                ON summary.run_id = runs.run_id
            WHERE summary.factor_id = %s
              AND summary.is_sub_factor_id = 1
            ORDER BY runs.created_at DESC, runs.id DESC
            """,
            (sub_factor_id,),
        )
        return [self._normalize_database_row(row) or {} for row in rows]

    def get_factor_refresh_validity_snapshots(
        self,
        sub_factor_id: int,
        registration_validity_status_id: int,
    ) -> list[dict[str, Any]]:
        """读取登记初始快照之外、且引用新版汇总结果的有效性快照。

        参数 ``sub_factor_id`` 是登记生成的复合子因子主键，``registration_validity_status_id`` 是登记响应中的初始
        ``factor_validity_status.id``。返回关联的有效性快照及其时序/截面汇总记录身份；查询排除登记初始快照和
        没有任何 summary 外键的登记占位状态；如果后端在原地更新登记快照并补上 summary 外键，该行会被保留。方法只读
        数据库，SQL 或连接异常直接向调用方抛出。
        """

        rows = self._client.fetch_all(
            """
            SELECT
                validity.*,
                validity.universe_key,
                validity.factor_bar_interval,
                validity.factor_window_bars,
                validity.return_bar_interval,
                validity.forward_return_bars,
                validity.window_scope,
                validity.period_start,
                validity.period_end,
                CASE WHEN validity.id = %s THEN 1 ELSE 0 END AS is_registration_snapshot,
                time_summary.run_id AS time_series_summary_run_id,
                time_summary.factor_id AS time_series_summary_factor_id,
                time_summary.is_sub_factor_id AS time_series_summary_is_sub_factor_id,
                time_summary.ic_scope AS time_series_summary_ic_scope,
                time_summary.calculation_mode AS time_series_summary_calculation_mode,
                time_summary.factor_bar_interval AS time_series_summary_factor_bar_interval,
                time_summary.factor_window_bars AS time_series_summary_factor_window_bars,
                time_summary.return_bar_interval AS time_series_summary_return_bar_interval,
                time_summary.forward_return_bars AS time_series_summary_forward_return_bars,
                time_summary.universe_key AS time_series_summary_universe_key,
                time_summary.symbol AS time_series_summary_symbol,
                time_summary.window_scope AS time_series_summary_window_scope,
                time_summary.metric_window_bars AS time_series_summary_metric_window_bars,
                time_summary.metric_window_days AS time_series_summary_metric_window_days,
                time_summary.period_start AS time_series_summary_period_start,
                time_summary.period_end AS time_series_summary_period_end,
                cross_summary.run_id AS cross_sectional_summary_run_id,
                cross_summary.factor_id AS cross_sectional_summary_factor_id,
                cross_summary.is_sub_factor_id AS cross_sectional_summary_is_sub_factor_id,
                cross_summary.ic_scope AS cross_sectional_summary_ic_scope,
                cross_summary.calculation_mode AS cross_sectional_summary_calculation_mode,
                cross_summary.factor_bar_interval AS cross_sectional_summary_factor_bar_interval,
                cross_summary.factor_window_bars AS cross_sectional_summary_factor_window_bars,
                cross_summary.return_bar_interval AS cross_sectional_summary_return_bar_interval,
                cross_summary.forward_return_bars AS cross_sectional_summary_forward_return_bars,
                cross_summary.universe_key AS cross_sectional_summary_universe_key,
                cross_summary.symbol AS cross_sectional_summary_symbol,
                cross_summary.window_scope AS cross_sectional_summary_window_scope,
                cross_summary.metric_window_bars AS cross_sectional_summary_metric_window_bars,
                cross_summary.metric_window_days AS cross_sectional_summary_metric_window_days,
                cross_summary.period_start AS cross_sectional_summary_period_start,
                cross_summary.period_end AS cross_sectional_summary_period_end
            FROM factor_validity_status AS validity
            LEFT JOIN factor_ic_summary_metrics AS time_summary
                ON time_summary.id = validity.time_series_summary_id
            LEFT JOIN factor_ic_summary_metrics AS cross_summary
                ON cross_summary.id = validity.cross_sectional_summary_id
            WHERE validity.factor_id = %s
              AND validity.is_sub_factor_id = 1
              AND (
                  validity.time_series_summary_id IS NOT NULL
                  OR validity.cross_sectional_summary_id IS NOT NULL
              )
            ORDER BY validity.updated_at DESC, validity.id DESC
            """,
            (registration_validity_status_id, sub_factor_id),
        )
        return [self._normalize_database_row(row) or {} for row in rows]

    def count_parent_relations_for_sub_factor(self, sub_factor_id: int) -> int:
        """统计登记生成子因子的母因子关联数量。

        参数 ``sub_factor_id`` 是组合报告登记接口生成的子因子主键。
        返回 ``factor_sub_factor_relations`` 中对应行数；调用方根据当前业务规则判断数量和来源是否符合预期。
        """

        row = self._client.fetch_one(
            "SELECT COUNT(*) AS record_count FROM factor_sub_factor_relations WHERE sub_factor_id = %s",
            (sub_factor_id,),
        )
        return int(row["record_count"]) if row is not None else 0

    def find_existing_sub_factor_name(self) -> str | None:
        """查找一个已存在的非空子因子名称用于名称冲突测试。

        不接收参数。
        返回一个子因子名称；表为空时返回 ``None``。
        """

        row = self._client.fetch_one(
            """
            SELECT sub_factor_name
            FROM sub_factors
            WHERE sub_factor_name IS NOT NULL
              AND TRIM(sub_factor_name) <> ''
            ORDER BY id ASC
            LIMIT 1
            """
        )
        return str(row["sub_factor_name"]) if row is not None else None

    def prepare_form_for_worker(self, form_id: int, pipeline_run_id: str, lock_pool: bool = True) -> dict[str, Any]:
        """将测试创建的表单准备为初始版本 Worker 回写前置状态。

        参数 ``form_id`` 是测试表单主键，``pipeline_run_id`` 是本次模拟 Worker 运行标识，``lock_pool`` 决定是否锁定因子池。
        返回更新后的表单字典；仅允许测试环境写入，表单或因子池不存在时抛出 ``RuntimeError``。
        """

        self._assert_test_write_allowed()
        with self._client.transaction() as transaction:
            form = transaction.fetch_one(
                """
                SELECT id, factor_combo_pool_id
                FROM factor_combo_form
                WHERE id = %s
                FOR UPDATE
                """,
                (form_id,),
            )
            if form is None:
                raise RuntimeError(f"Factor combo form does not exist: {form_id}")
            pool_id = int(form["factor_combo_pool_id"])
            if lock_pool:
                result = transaction.execute(
                    "UPDATE factor_combo_pool SET status = %s WHERE pool_id = %s",
                    ("locked", pool_id),
                )
                if result.rowcount != 1:
                    raise RuntimeError(f"Factor combo pool does not exist: {pool_id}")
            transaction.execute(
                """
                UPDATE factor_combo_form
                SET status = %s,
                    pipeline_run_id = %s,
                    factor_combo_id = NULL,
                    factor_combo_experiment_info_id = NULL
                WHERE id = %s
                """,
                ("processing", pipeline_run_id, form_id),
            )
        prepared_form = self.get_form(form_id)
        if prepared_form is None:
            raise RuntimeError(f"Prepared factor combo form cannot be read: {form_id}")
        return prepared_form

    def set_form_status_for_test(self, form_id: int, status: str) -> dict[str, Any]:
        """只修改测试表单状态以构造单一前置条件冲突。

        参数 ``form_id`` 是自动化创建的表单主键，``status`` 必须是表结构允许的状态。
        返回更新后的表单字典；非测试环境、非法状态或表单不存在时抛出 ``RuntimeError``。
        """

        allowed_statuses = {"draft", "submitted", "processing", "completed", "failed"}
        if status not in allowed_statuses:
            raise ValueError(f"Unsupported factor combo form status: {status}")
        self._assert_test_write_allowed()
        result = self._client.execute(
            "UPDATE factor_combo_form SET status = %s WHERE id = %s",
            (status, form_id),
        )
        if result.rowcount != 1:
            raise RuntimeError(f"Factor combo form does not exist: {form_id}")
        form = self.get_form(form_id)
        if form is None:
            raise RuntimeError(f"Updated factor combo form cannot be read: {form_id}")
        return form

    def set_form_pipeline_run_for_test(self, form_id: int, pipeline_run_id: str) -> dict[str, Any]:
        """只修改测试表单的 Pipeline Run ID，以隔离运行关联校验场景。

        参数 ``form_id`` 是自动化创建的表单主键，``pipeline_run_id`` 是测试用的非空运行标识。
        返回更新后的表单字典；非测试环境、运行标识为空、长度超限或表单不存在时抛出异常。
        """

        normalized_pipeline_run_id = pipeline_run_id.strip()
        if not 1 <= len(normalized_pipeline_run_id) <= 255:
            raise ValueError("pipeline_run_id must contain 1 to 255 non-whitespace characters")
        self._assert_test_write_allowed()
        result = self._client.execute(
            "UPDATE factor_combo_form SET pipeline_run_id = %s WHERE id = %s",
            (normalized_pipeline_run_id, form_id),
        )
        if result.rowcount != 1:
            raise RuntimeError(f"Factor combo form does not exist: {form_id}")
        form = self.get_form(form_id)
        if form is None:
            raise RuntimeError(f"Updated factor combo form cannot be read: {form_id}")
        return form

    def set_pool_status_for_test(self, pool_id: int, status: str) -> dict[str, Any]:
        """只修改测试因子池状态以构造锁定状态冲突。

        参数 ``pool_id`` 是自动化创建的因子池主键，``status`` 必须是表结构允许的状态。
        返回更新后的因子池字典；非测试环境、非法状态或因子池不存在时抛出 ``RuntimeError``。
        """

        allowed_statuses = {"draft", "locked", "archived"}
        if status not in allowed_statuses:
            raise ValueError(f"Unsupported factor combo pool status: {status}")
        self._assert_test_write_allowed()
        result = self._client.execute(
            "UPDATE factor_combo_pool SET status = %s WHERE pool_id = %s",
            (status, pool_id),
        )
        if result.rowcount != 1:
            raise RuntimeError(f"Factor combo pool does not exist: {pool_id}")
        pool = self.get_pool(pool_id)
        if pool is None:
            raise RuntimeError(f"Updated factor combo pool cannot be read: {pool_id}")
        return pool

    def claim_feedback_for_worker(self, feedback_id: int) -> dict[str, Any]:
        """将测试创建的 pending Feedback 标记为已被模拟 Worker 认领。

        参数 ``feedback_id`` 是需要认领的反馈主键。
        返回更新后的反馈字典；仅允许测试环境写入，反馈不存在时抛出 ``RuntimeError``。
        """

        self._assert_test_write_allowed()
        result = self._client.execute(
            """
            UPDATE factor_combo_experiment_feedback
            SET status = %s,
                claimed_at = UTC_TIMESTAMP(3)
            WHERE id = %s
              AND status = %s
            """,
            ("processing", feedback_id, "pending"),
        )
        if result.rowcount != 1:
            raise RuntimeError(f"Pending factor combo feedback cannot be claimed: {feedback_id}")
        feedback = self.get_feedback(feedback_id)
        if feedback is None:
            raise RuntimeError(f"Claimed factor combo feedback cannot be read: {feedback_id}")
        return feedback



















    @staticmethod
    def _placeholders(values: Sequence[Any]) -> str:
        """为固定长度的 MySQL IN 条件生成参数占位符。

        参数 ``values`` 是需要绑定的值集合。
        返回对应数量的 ``%s`` 占位符字符串；空集合时抛出 ``ValueError``，避免生成无效 SQL。
        """

        if not values:
            raise ValueError("Cannot build SQL placeholders for an empty collection")
        return ", ".join("%s" for _ in values)

    @staticmethod
    def _to_sub_factor_choice(row: Mapping[str, Any] | None) -> SubFactorChoice | None:
        """将子因子查询结果转换为 ``SubFactorChoice``。

        参数 ``row`` 是数据库返回的一行或 ``None``。
        返回转换后的选择对象；没有记录时返回 ``None``。
        """

        if row is None:
            return None
        return SubFactorChoice(
            sub_factor_id=int(row["sub_factor_id"]),
            sub_factor_name=str(row["sub_factor_name"]),
            parent_factor_id=int(row["parent_factor_id"]),
            parent_factor_name=str(row["parent_factor_name"]),
        )

    @staticmethod
    def _normalize_json_columns(row: dict[str, Any] | None, *column_names: str) -> dict[str, Any] | None:
        """将 MySQL JSON 字段统一转换为 Python 对象。

        参数 ``row`` 是数据库查询结果，``column_names`` 是需要解析的 JSON 列名。
        返回复制后的字典或 ``None``；JSON 内容无效时保留原值，便于测试直接报告数据库异常数据。
        """

        if row is None:
            return None
        result = dict(row)
        for column_name in column_names:
            value = result.get(column_name)
            if isinstance(value, str):
                try:
                    result[column_name] = json.loads(value)
                except json.JSONDecodeError:
                    pass
        return result

    @staticmethod
    def _normalize_database_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
        """标准化完整实体查询返回的 JSON 列。

        参数 ``row`` 是 ``SELECT table.*`` 或关联查询返回的一行数据库记录。返回复制后的字典，并解析所有明确的
        JSON 配置、快照和指标列；普通文本字段保持原样。解析失败时保留原始字符串，交由上层对账报告数据库脏数据，
        不会把异常转换成空对象或静默忽略。
        """

        if row is None:
            return None
        json_columns = {
            column_name
            for column_name in row
            if column_name.endswith("_json")
            or column_name in {"metadata", "universe_symbols", "config_json"}
        }
        return FactorComboRepository._normalize_json_columns(row, *sorted(json_columns))

    def _assert_test_write_allowed(self) -> None:
        """阻止在非测试环境执行组合因子测试数据写入。

        不接收参数。
        不返回值；当前环境不是 ``test`` 时抛出 ``RuntimeError``，避免误删或伪造非测试环境业务数据。
        """

        if self._environment != "test":
            raise RuntimeError(
                f"Factor combo database writes are allowed only in the test environment, current: {self._environment!r}"
            )
