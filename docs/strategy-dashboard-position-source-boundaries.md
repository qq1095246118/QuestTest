# 当前持仓原始数据验证：通过项与未接通项

## 已接通并通过

正式入口：`tests/cases/strategy_dashboard/test_source_position_boundaries.py`。

以下三个既有 canonical 场景的原始黄金预期未修改：

| 场景 | 验证 |
| --- | --- |
| FXT-POSITION-001 | 完整空持仓集合返回持仓数 0、毛名义 0 |
| FXT-POSITION-002 | 原始负数量空头保留为有效持仓，毛名义取绝对值 800 |
| FXT-POSFIELDS-001 | 原始浮盈 0.00000003 与 -0.00000002 合计 0.00000001，保证金 0.00000002 与 0.00000003 合计 0.00000005 |

FXT-POSFIELDS-001 原场景只提供被测 U/margin 字段，真实当前持仓入口还需要数量和名义字段。独立 helper `complete_position_financial_inputs` 只对未提供的这两个非被测字段补固定原始输入，不覆盖已有键（包括 null），不做求和、去重或过滤。报告同时保留原场景和实际完整输入。

另有 8 个 `observed_implementation` profile，独立保存在 `tests/data/strategy_dashboard/position_source_profiles.json`，不并入 canonical 黄金场景数：

| 场景 | 验证 |
| --- | --- |
| FIP-EXPOSURE-001 | 多 600、空 -400、保证金 100+200、权益 1000：毛名义 1000、净名义 200、净敞口率 0.2、保证金使用率 0.3 |
| FIP-EXPOSURE-002 | 单空头 -400：毛名义 400、净名义 -400、净敞口率 -0.4；方向不被绝对值抹掉 |
| FIP-EXPOSURE-003 | 同合约等额多空：净名义为 0，毛名义 1200、保证金 150 不归零 |
| FIP-EXPOSURE-004 | 权益为 0，仅权益分母的比率不可计算，原始金额继续返回 |
| FIP-EXPOSURE-005 | 权益为负数，保证金使用率与净敞口率不可计算 |
| FIP-EXPOSURE-006 | 权益缺失，金额仍可计算，比率不可计算 |
| FIP-EXPOSURE-007 | 一条保证金缺失：保证金总额及使用率不可用，不影响毛名义和净名义 |
| FIP-EXPOSURE-008 | 完整空持仓、正权益：金额和比率均完整为 0 |

这些 profile 核验接口声明的 `basis=gross_notional_v1` 当前实现，其中净敞口率保留正负方向。它们不把文档尚待决定的跨合约风险抵消口径签认为正式业务规则。

2026-09-23 已执行全部 11 项。首轮 9 通过、2 项暴露测试输入/实施预期问题；补齐无关源字段和校准当前版本的有符号比率后，针对两项重新执行均通过。最新每项证据都是 `assertion_passed=true` 且 `cleanup_verified_zero=true`。

证据：

- `reports/quant-position-source-boundaries.xml`：首轮 11 项，保留原始两项未通过记录。
- `reports/quant-position-source-boundaries-rerun.xml`：只复验修正的 2 项，2 passed。
- `reports/quant-raw-position-72c9ef0970e34e8f9cb54c6041ea7a3e/`：首轮原始输入、实际返回及清理。
- `reports/quant-raw-position-b7b09add34384f43bdf09f149ed38d1f/`：两项最终通过证据。

## 尚未接通，不算产品 Bug 或已完成覆盖

FM-POSITION-004 已用真实隔离多账户分别输入同合约 BTC 多头 200/300。账户汇总识别到两个账号，但 root 及 minute 双账户持仓消费路径均返回 `position_count=null`、金额 `DEPENDENCY_INCOMPLETE`，尚未证明适配器完整绑定到真正组合持仓入口。因此保持 `source_adapter_unbound`，不新增重复正式测试，不断言这是产品算错。

2026-09-24 收尾检查：前序三个探针的六个账号及其 view/run/definition/batch/snapshot 均重新 DB 查询为 0；未残留合成数据。最后两次鉴权在任何 DB 写入之前被 TLS EOF 阻断，未创建新 fixture。探针正式 test 与仅为探针创建的 service/repository 已删除，保留所有报告证据：

- `reports/quant-multiaccount-position-87390a7b6ebe4ae0a05fd5c7fdd4aa95.json`
- `reports/quant-multiaccount-position-54dbc31225ce4f50b3a6f4797bc55014.json`
- `reports/quant-multiaccount-position-8a52b2432b124654b9a1d9e0addeec28.json`

以下其余 canonical 场景不应通过当前已归一持仓集合“伪覆盖”：

| 场景 | 精确缺口 |
| --- | --- |
| FM-POSITION-002、003 | 当前集合没有历史 timestamp 消费契约；不能据此证明历史重复快照去重或最新零持仓覆盖旧持仓 |
| FM-POSITION-006、008 | 缺少混币原始数据的实际入口/换算或筛选契约，不把未知 currency JSON 字段塞入后求和当成真实混币测试 |
| FM-POSITION-007 | 当前来源模型未提供 spot/perpetual 的独立身份消费字段 |
| FXT-POSITION-003 | 当前来源模型未证明消费独立 position_id，同合约同方向的不同来源身份不能仅靠自定义字段证明 |
| FM-POSFIELDS-003 | 当前 margin_used 字段无 initial/maintenance 分类消费契约，不能凭添加未知 margin_type 声称验证禁止混加 |
