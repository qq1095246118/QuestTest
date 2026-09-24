# 总览组合金额与收益率：受控原始数据验证

本组直接向测试环境创建隔离账号、已结束的账号策略关系和原始账户读取视图，再调用已部署的 `GET /quantitative-trading/analytics/summary?subject_type=portfolio`。注入的是逐账户 E/W/U/G/D 等输入字段，不包含组合金额、收益率或期望结果；每条用例结束后精确清理并重新查询确认没有残留。

这些是独立的后端实现 profile，保存在 `tests/data/strategy_dashboard/portfolio_source_profiles.json`，不与主公式黄金场景重复统计。它们证明采集后源字段到后端输出的计算，不能代替 NT/交易所采集器或 `metric_source=prometheus` 页面来源的全链路验收。

| Profile | 场景 | 主要断言 |
| --- | --- | --- |
| FM-PORTFOLIO-LIVE-001 | 两个不同账户 | E、W、U、G 分别求和；账户数为 2 |
| FM-PORTFOLIO-LIVE-002 | 零权益和负权益 | 保留两个账户；负数按实际符号参与汇总 |
| FM-PORTFOLIO-LIVE-003 | 两个全零账户 | 返回完整零值；不当成数据缺失 |
| FM-PORTFOLIO-LIVE-004 | 十亿级金额与小数点后 8 位 | 大额和极小正负金额相加保持后端数值精度 |
| FM-PORTFOLIO-LIVE-005 | 一账户 E 缺失 | E 不得返回伪装完整的总额；W/U/G 保持可计算 |
| FM-PORTFOLIO-LIVE-006 | 一账户 W 缺失 | W 的缺失不污染 E/U/G |
| FM-PORTFOLIO-LIVE-007 | 一账户 U 缺失 | 缺失未实现收益不得当成完整零；其他金额保留 |
| FM-PORTFOLIO-LIVE-008 | 一账户 G 缺失 | 缺失已实现收益不得当成完整零；其他金额保留 |
| FM-PORTFOLIO-LIVE-009 | 读取视图重复引用同一账户 | 账户和金额各计算一次 |
| FM-PORTFOLIO-LIVE-010 | 只筛选第一个账户 | 第二个账户的全部金额不进入总览 |
| FM-PORTFOLIO-LIVE-011 | 只筛选第二个亏损账户 | 返回其完整金额和负收益 |
| FM-PORTFOLIO-LIVE-012 | 同一账户绑定两个不同策略 | 实际创建两条已结束关系，物理账户和金额仍只统计一次 |
| FM-PORTFOLIO-LIVE-013 | 不同本金账户的组合累计收益率 | G=[10,900]、D=[1000,10000]，返回 910/11000=0.08272727，不能简单平均两个账户比率 |
| FM-PORTFOLIO-LIVE-014 | 两账户正负 G 完全抵消 | 组合累计收益率为完整零 |
| FM-PORTFOLIO-LIVE-015 | 两账户整体亏损 | (-100-450)/(1000+10000)=-0.05 |
| FM-PORTFOLIO-LIVE-016 | 全部 G 为零且 D 为正 | 累计收益率是完整零值 |
| FM-PORTFOLIO-LIVE-017 | 一账户 G 缺失 | 组合累计收益率不能伪装完整值 |
| FM-PORTFOLIO-LIVE-018 | 组合 D 总额为零 | 收益率不可计算，并有明确状态和原因 |

收益率六项针对接口明确返回的 `exchange_cumulative_realized_latest_net_deposit_v1` 版本验证 `ΣG/ΣD`。这是已部署计算版本的一致性检查，不代表其他来源的组合加权方法已经完成业务签认。

执行命令（测试配置从本机项目配置读取，不在命令中放凭据）：

```bash
QUEST_FORMULA_HTTP_PROXY=http://127.0.0.1:7890 python3 -m pytest \
  tests/cases/strategy_dashboard/test_source_portfolio_aggregation.py \
  --live --env test -p no:cacheprovider -q \
  --junitxml=reports/quant-portfolio-source-profiles.xml
```

每个 profile 都在 `reports/quant-portfolio-source-FM-PORTFOLIO-LIVE-*.json` 留存原始输入、隔离账号/关系标识、请求、完整返回、固定预期和清理验证。`--live` 未指定时明确跳过，不作为通过。

2026-09-23 已对最终 18 个 profile 执行真实后端验证，全部通过。金额 001～011、重新设计后的真实多策略绑定 012、收益率 013～018 的证据分别保留；早期适配探路失败记录也保留，不算产品 Bug。早期“重复 account_ids 必须自动去重”假设被接口明确的参数约束否定，最终 012 使用真正的物理账户多策略绑定场景。
