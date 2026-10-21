# 账号管理自动化覆盖

账号管理用例以全账号集合为主，单账号只作为基础映射控制。测试数据使用 `qam_<UUID>` 和 `read_<UUID>` 唯一标识，fixture 只在测试环境创建，并在测试结束后按所有权清理。

## 已覆盖

- 保证金、未实现盈亏、已实现盈亏、总手续费、累计资费的多账号金额汇总。
- 总收益率的汇总收益 `G`、汇总有效本金 `D`、确认入金/出金、正负收益、0值、缺失值和非正本金。
- 聚合年化收益率的不足7天/恰好7天/超过7天边界，以及聚合权益序列的当前回撤峰值逻辑。
- 同策略多账号、不同交易所和运行状态下的全账号集合纳入；策略切换只保留为数据模型待验证项，不把静态账号金额样例误报成切换链路覆盖。
- 重复账号身份、账号顺序变化、空账号集合、NaN/Infinity 和源字段缺失。
- 账号管理目录和 portfolio summary 的 HTTP 参数契约。
- 测试环境多账号 fixture 的批次、快照、读取视图和精确清理。
- 真实 fixture 先核对目录成员集合，再核对 portfolio 摘要，避免只验证汇总数字而遗漏账号范围。

## 运行

账号管理只执行真实测试环境链路，需显式启用测试环境：

```bash
pytest -q --live --env test -m account_management
```

真实用例只写入带 `qam_` 前缀的账号、快照、批次和读取视图；如果部署环境没有消费该 fixture 的 source/read-view 合约，用例直接失败并保留 `BLOCKED_CONTRACT` 诊断，不会把接口未接入误判为通过。

当前账号目录 API 只使用接口文档已确认的 `runtime_status`、`account_ids` 和 `exchange` 查询参数；账号名称、商务、策略名称、研究员的具体查询字段尚未在接口契约中绑定，因此没有在 API 层猜测参数名。

## 覆盖审计

离线 Case 已移除。真实测试入口包含多账号目录成员核对、金额字段、总收益率和缺失收益源场景，必须显式使用 `--live --env test`；fixture 未被后端消费时按失败处理。

以下项目仍需真实接口/数据库契约后再计为完整覆盖：

- 超过100个账号的全量分页和空目录集合；
- 账号名称、商务、策略名称、研究员的查询参数；当前接口文档只确认交易所、状态和账号ID参数；
- 年化收益率、实时回撤从真实多账号源输入到接口输出的端到端断言；当前已有独立 Oracle 边界；
- A→B→A策略迁移、确认资金流、迟到/更正事件在真实源表到聚合结果之间的完整消费链路；
- 重复批次、`source_generation`、`generation`、`snapshot_id`冲突的真实拒绝或部分状态契约；
- 手续费/资费缺失、重复、迟到和修订的真实接口状态断言。
- “保证金”是否对应 `margin_equity`，以及接口指标 `cumulative_return` 是否就是页面“总收益率”，仍需和后端字段字典绑定。
- 当前回撤 Oracle 默认输入已经按时间排序；真实源数据需要验证时间排序、重复时间点和聚合权益序列的来源。

这些项目在绑定前保持“待契约/待实跑”，不把独立预期计算或 fixture 写入成功误报为真实业务覆盖。

## 分层

- `api/account_management_api.py`：账号目录和 portfolio 摘要协议。
- `service/account_management_service.py`：独立聚合 Oracle，不读取页面结果。
- `db/account_management_fixture_repository.py`：测试环境多账号数据写入和精确清理。
- `service/account_management_source_flow.py`：多账号 fixture 到摘要接口的编排。
- `tests/cases/account_management/`：真实测试环境入口。
