"""Service 层示例：按业务动作编排 Repository 调用。"""

from __future__ import annotations

from db.repository import SampleRecord, SampleRecordRepository


class SampleRecordService:
    """演示 Service 如何提供清晰业务语义且不依赖具体测试用例。"""

    def __init__(self, repository: SampleRecordRepository) -> None:
        """初始化示例业务服务。

        参数 ``repository`` 是示例记录的数据访问对象。
        不返回值；服务通过该仓储处理记录生命周期。
        """

        self._repository = repository

    def register_record(self, name: str) -> SampleRecord:
        """校验名称后创建一条可供后续业务流程使用的记录。

        参数 ``name`` 是调用方提供的记录名称。
        返回新建的 ``SampleRecord``；名称为空白时抛出 ``ValueError``。
        """

        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Sample record name cannot be blank")
        return self._repository.create(normalized_name)

    def remove_record(self, record_id: int) -> bool:
        """清理一个由测试创建的示例记录。

        参数 ``record_id`` 是待清理记录的主键。
        返回是否实际删除了该记录；该方法应仅用于允许写入的测试环境。
        """

        return self._repository.delete_by_id(record_id)
