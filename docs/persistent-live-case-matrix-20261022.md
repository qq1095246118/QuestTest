# 三个看板模块真实接口用例矩阵

日期：2026-10-22

本矩阵只登记走真实 HTTP GET 的回归用例。数据由策略侧一次性数据集准备，开发侧首次全量拉取并发布 generation 后，由 `persistent_data_preflight` 只读检查；Case 不插入、更新、删除业务数据，也不创建 `quant_read_views`、账号快照或手工 collection batch。

## 1. 统一链路

```text
策略侧源表一次性造数
  -> 开发侧首次全量拉取/计算/发布
  -> persistent_data_preflight 按 dataset_id 检查主体、源样本、generation 和资源
  -> Case 只调用真实无 snapshot_id GET
  -> 断言 HTTP、success、mock=false、主体、snapshot/generation、字段状态和数据容器
```

如果某条用例需要的稳定账号、策略、样本或资源不存在，只跳过该条并输出缺口；不能用临时 fixture 补数，也不能把 skipped 计为通过。

## 2. 账号详情

| Case | 接口 | 场景 | 前置能力 | 主要断言 |
|---|---|---|---|---|
| ADC-LIVE-005 | N01 summary | 页面默认单账号读取 | account_detail、performance_samples、published_generation | account 主体、`mock=false`、snapshot、金额指标 |
| ADC-LIVE-006 | N01 summary | 16 个核心指标状态追踪 | 同上 | 每个指标有结构化对象；null 必须有 reason/status |
| ADC-LIVE-007 | N01 summary | 日期区间读取 | 同上 | 日期请求保留账号主体和真实 snapshot |
| ADC-LIVE-008 | N01 summary | 重复读取 | 同上 | 两次真实读取均可追踪且不使用 mock |
| ADC-LIVE-009 | N01 summary | 不存在账号 | 无业务数据前置 | 400/404 和结构化业务错误 |
| ADC-LIVE-010 | N01 summary | 逆序日期 | 有效账号主体 | 400、`INVALID_ARGUMENT` |

金额正负、真实 0、缺失、年化/风险指标不足等状态统一在 ADC-LIVE-006 的持久化数据中验证；若要验证特定数值，需在 manifest 中绑定源行和期望值，不能在 Case 中临时造数。

## 3. 账号管理

| Case | 接口 | 场景 | 前置能力 | 主要断言 |
|---|---|---|---|---|
| AM-DATA-001 | `/admin/quant-accounts` | 多账号目录、身份和逐行指标 | account_management、multi_account、published_generation | 目录至少两个数据集账号、身份可追踪、真实 snapshot |
| AM-DATA-002 | analytics summary | portfolio 16 指标 | account_management、multi_account、performance_samples、published_generation | portfolio 主体、指标完整、状态和原因明确 |
| AM-DATA-003 | analytics summary | 全账号总收益率 | 同上 | 总收益率为结构化真实值，不平均目录展示值 |
| AM-DATA-004 | analytics summary | 指定数据集账号集合 | account_management、multi_account、published_generation | `account_ids` 原样发送，仍返回 portfolio 维度 |
| AM-PROTOCOL-001 | `/admin/quant-accounts` | 默认页面请求 | live/test | 无 `snapshot_id`、参数完整、非 mock |
| AM-PROTOCOL-002 | `/admin/quant-accounts` | 未知账号筛选 | live/test | 空集合，不回退全量 |

多账号正负收益、同策略多账号、一账号切换策略和停止区间不在接口 Case 中临时构造；这些情况必须由同一持久化数据集的账号/策略归属和分钟事实承载，preflight 负责检查 `multi_account`、`same_strategy_multi_account`、`account_strategy_switch` 和 `multi_interval` 能力。

## 4. 策略详情

| Case | 接口 | 场景 | 前置能力 | 主要断言 |
|---|---|---|---|---|
| SD-LIVE-001 | analytics summary | 当前核心 16 指标 | strategy_detail、performance_samples、published_generation | 策略主体、指标状态、null 原因 |
| SD-LIVE-002 | analytics summary | 历史日期区间 | strategy_detail、historical_samples、published_generation | 策略维度、from/to、区间数据 |
| SD-LIVE-003 | strategy definition | 策略定义 | strategy_detail | 返回指定 strategy_id |
| SD-LIVE-004 | strategy accounts | 账号归属区间 | strategy_detail、assignments | 账号关系和主体可回溯 |
| SD-LIVE-005 | analytics risk | 风险指标 | strategy_detail、performance_samples、published_generation | 真实 risk 容器和主体 |
| SD-LIVE-006 | analytics series | 净值/回撤曲线 | strategy_detail、curves、published_generation | 曲线非空、真实主体 |
| SD-LIVE-007 | trading positions/signals/batches/plans/intents/orders/fills | 交易资源 | 对应 positions/signals/orders/fills 能力 | 每个资源通过真实 GET 返回 JSON 容器、主体一致 |
| SD-LIVE-008 | analytics summary | 页面默认无 snapshot 输入 | strategy_detail | 请求参数不携带 snapshot_id |
| SD-LIVE-009 | trading positions | 已发布但集合可为空 | strategy_detail、published_generation | 空集合也必须是 JSON 容器，不返回 null |

交易资源必须由同一稳定父子链准备：`signal -> outcome/batch -> rebalance_plan -> order_intent -> order -> fill -> position/performance`。接口 Case 不把孤立列表或本地 Oracle 当成链路通过证据。

## 5. 删除和迁移清单

以下旧 Case 已从真实回归入口删除：

- 账号详情中调用 `AccountDetailSourceFlow.execute()` 的持仓、金额、0 值和缺失字段 fixture Case；
- 账号管理中调用 `AccountManagementSourceFlow.execute()` 的 `_accounts()` 多账号 fixture、金额汇总、分页和交易所过滤 Case；
- 策略详情中调用 `StrategyDetailSourceFlow.execute()` / `execute_resource()` 的 `test_strategy_detail_live.py` 全部 qsd/read-view fixture Case；
- Case 及 fixture 中直接依赖 `FixtureRepository`、`quant_read_views`、`quant_account_snapshots`、`quant_collection_batches` 的数据断言。

旧 source flow/repository 已从三个模块和代码树删除。数据初始化工具必须在独立的 seed 命令中运行，并在写入后明确调用开发侧全量拉取/发布入口。

## 6. 完成判定

只有同时满足以下条件，才能把本轮三模块标记为真实接口回归完成：

1. manifest 中四个账号、三个策略、运行区间和父子事实链存在；
2. preflight 找到数据集标签、源水位和已发布 generation；
3. 三个模块 Case 均实际发出 HTTP GET，响应为 `success=true`、`mock=false`；
4. 账号、策略和区间主体没有串维度；
5. 核心指标、资源容器、空/缺失状态和错误码断言通过；
6. 被删除的旧 fixture Case 不再被 pytest 收集。
