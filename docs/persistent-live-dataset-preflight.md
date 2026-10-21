# 三个量化页面的持久化数据前置

账号管理、账号详情和策略详情的 live case 共用策略侧持久化数据。数据由单独的
seed 步骤写入，不在每条 pytest 用例中插入或删除。默认数据集标签是
`qta_persist_20261022`，执行时可以使用 `AUTOMATION_PERSISTENT_DATASET_ID` 覆盖；
`AUTOMATION_LIVE_DATASET_TAG` 仍作为兼容别名。
如果需要检查固定主体没有被删除，可再设置逗号分隔的
`AUTOMATION_LIVE_STRATEGY_IDS` 和 `AUTOMATION_LIVE_ACCOUNT_IDS`；未设置时只按
数据集标签发现主体。

`tests/conftest.py` 中的 `persistent_data_preflight` 使用只读数据库连接，按标签
检查策略定义、账号运行关系、NT assignment、已发布的性能样本和 generation。
持仓批次、信号、订单、成交等资源按部署中已发布的 MySQL 投影表逐项检查；这些表
在部分部署中由 NT/PG 事实表提供，因此缺表只会阻断对应资源用例。preflight 只生成
一次数据集快照，不会触发 collector、重算或业务写入。

用例按自身场景声明前置：

```python
persistent_data_preflight.require(
    "strategy_detail", "performance_samples", "published_generation",
    case_id="SD-LIVE-001"
)
```

缺少前置时抛出 `PersistentDataBlocked`（继承 `SkipTest`），pytest 将该条用例标记
为 skipped，并保留缺少的能力、数据集标签、主体数量和源表计数。典型能力包括：

- `account_management`、`account_detail`、`strategy_detail`
- `multi_account`、`same_strategy_multi_account`、`account_strategy_switch`、`multi_interval`
- `performance_samples`、`positions`、`trading_events`、`signals`、`orders`、`fills`

seed 需要至少在策略定义的 `id/code/name`、运行关系的 `definition_id`、以及策略
源事实的 `definition_id` 上保持同一标签可追踪；每次 seed 完成后不再由测试执行阶段
清理，避免开发侧只增量更新导致新插入的历史数据无法被采集。写入源事实后必须
执行开发侧正式的首次全量拉取/计算/发布任务，并确认 `published_generation` 后再
运行数据用例；只有源表行而没有 generation 时会以 `PERSISTENT_DATASET_NOT_PUBLISHED`
跳过，而不会把源表插入误判为接口通过。
