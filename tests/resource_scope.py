"""pytest 用例资源归属和异步资源保护状态。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TestResourceScope:
    """记录单个 pytest 用例创建的资源及暂时不可清理的表单。

    参数由 pytest Fixture 和 Service 流程共同填充；返回的资源图只包含当前 Scope 登记的会话及表单，
    供 Fixture 在测试结束时交给数据库 Repository 清理。受保护表单仍可能被外部 Pipeline 使用，不会进入清理图。
    """

    session_ids: set[int] = field(default_factory=set)
    form_ids: set[int] = field(default_factory=set)
    protected_form_ids: set[int] = field(default_factory=set)
    session_forms: dict[int, set[int]] = field(default_factory=dict)

    def track_session(self, session_id: int) -> None:
        """登记一个由当前测试创建的 Factor 会话。

        参数 ``session_id`` 是创建接口返回的会话主键。不返回值；会话可在 Fixture 清理阶段进入删除候选。
        """

        normalized_session_id = int(session_id)
        self.session_ids.add(normalized_session_id)
        self.session_forms.setdefault(normalized_session_id, set())

    def track_form(self, session_id: int, form_id: int) -> None:
        """登记一个属于当前测试会话的组合表单。

        参数 ``session_id`` 是表单所属会话主键，``form_id`` 是提交接口返回的表单主键。
        不返回值；会话未由当前 Scope 登记时忽略该表单，避免把外部资源加入清理范围。
        """

        normalized_session_id = int(session_id)
        normalized_form_id = int(form_id)
        if normalized_session_id not in self.session_ids:
            return
        self.form_ids.add(normalized_form_id)
        self.session_forms.setdefault(normalized_session_id, set()).add(normalized_form_id)

    def protect_form(self, form_id: int) -> None:
        """标记仍可能被真实 Pipeline 使用的表单，禁止 Fixture 自动清理。

        参数 ``form_id`` 是已启动真实 Run 的表单主键。不返回值；保护标记会持续到 Service 确认安全终态。
        """

        self.protected_form_ids.add(int(form_id))

    def release_form(self, form_id: int) -> None:
        """解除已进入安全终态表单的保护标记。

        参数 ``form_id`` 是当前 Scope 中的表单主键。不返回值；未登记的 ID 会被静默忽略。
        """

        self.protected_form_ids.discard(int(form_id))

    def cleanable_resource_graph(self) -> dict[int, set[int]]:
        """生成供 Repository 使用的会话到表单归属图。

        不接收参数。返回 ``{session_id: {form_id, ...}}``；受保护表单所属会话不会进入结果，
        因而 Fixture 不会在异步流程仍可能运行时删除整个会话。
        """

        return {
            session_id: set(self.session_forms.get(session_id, set())) - self.protected_form_ids
            for session_id in self.session_ids
            if not set(self.session_forms.get(session_id, set())).intersection(self.protected_form_ids)
        }

    def cleanable_form_ids(self) -> set[int]:
        """返回当前 Scope 中未受保护的表单主键集合。

        不接收参数。返回值仅用于兼容旧的离线断言；Fixture 应优先使用 ``cleanable_resource_graph`` 保留归属关系。
        """

        return {
            form_id
            for form_ids in self.cleanable_resource_graph().values()
            for form_id in form_ids
        }

    def cleanable_session_ids(self) -> set[int]:
        """返回当前 Scope 中没有受保护表单的会话主键集合。

        不接收参数。返回值仅用于兼容旧的离线断言；实际清理应传递完整归属图。
        """

        return set(self.cleanable_resource_graph())
