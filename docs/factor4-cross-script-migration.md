# Factor 4.0 跨模块脚本迁移

本批把 7 个历史脚本的业务分支迁为 `tests/cases/factor4/` 中的独立用例。
`service/factor4_case_registry.py` 保存跨模块函数链接；`ASSERTED` 只表示已实现替代，
不表示测试环境结果通过。正式业务 Case 不读取历史报告，也不导入临时 runner。

## 分支归属

| 历史脚本 | 正常业务分支 | 异常、兼容、规范分支 |
| --- | --- | --- |
| critical_readonly_gap_probe.py | fact/forecast 全字段 DB、分页、available_at；六概率；推荐发布 PIT、route_count；metrics 自动/显式 batch、label、TS/CS；tags 路由身份/值 | 无/错误/畸形鉴权、未知 batch/scope、非法 limit，独立 deferred |
| cross_invariants_readonly.py | SCH-001/004 approved 版本、字段全集、mapping/resolution/replay DB；SCH-002 VWAP 四依赖和单字段选择；三次重读；公式已审批输入；publication/tags/metric 关联；feedback 所有权 | SCH-003/005/006/007 未知字段/版本/额外字段，独立 deferred |
| current_endpoint_deep_regression.py | daily 排序/去重/日期/六概率；forecast 三点到 recommendation；TS scope/search 与 CS 对照；metrics/validity/batch/slices；公式 AST 分类、精确 Run 与无缓存重放；环境 tags/metrics；schema/universe | 非法工具/必填/未知字段与 universe，独立 deferred；overall 使用已确认 TS 或 CS 任一有效规则 |
| current_endpoint_functional_probe.py | 目录完整分页/统计/第一页和游标重放/筛选交集/中文名/updated_after；详情层级和 batch；独立 research search、Run 选择、排名、指标、validity、切片、公式；KB/universe/schema/daily/推荐 | 缺失 ref 与未知 universe，按独立业务/异常 Case 承接 |
| direct_functional_deep.py | TS/CS summary 全字段；child_aggregate；单维有效性；混合 batch；完整 slices；精确公式/Run completion PIT；环境 publication/metrics/tags；daily revisions；universe/feedback；catalog/rank/KB | JSONRPC/鉴权/非法参数/未知 scope/游标篡改/三并发 exact metrics，独立 deferred |
| functional_extra_r0.py | 初始化/重连；schema default/explicit；三层详情与 batch；metrics/validity/slices；环境筛选/发布 PIT；universe/date/KB；精确 formula expression/hash | 无/坏鉴权、未知 schema/实体/scope/universe、无效日期/工具/参数/窗口、畸形 JSON，独立 deferred |
| tool_matrix_pit_probe.py | 每个只读工具的独立真实业务 Case；scope/search/stats/rank、metrics/formula/validity/slices 的 Run completion PIT；daily/recommendation/universe 的可见时间 | 严格双表示、当前 library_status 非 PIT 提示、write 工具仅声明审计，独立 deferred，不调用反馈写接口 |

## 本批新增

- `test_schema_business.py`：7 个正常实例，4 个 deferred 实例。验证默认/显式版本、全字段集合、replay `case_key` 和完整数据、单字段选择与依赖、三次实读。
- `test_cross_read_business.py`：10 个正常实例，7 个 deferred 实例。验证首个环境发布的三点边界、三个真实 forecast 时点的全部发布分区、精确公式三次无缓存读取、未知环境选择器和规范提示。
- API 只封装 schema 端点；Schema Repository 在只读一致性事务读取数据库；Service 返回差异，Case 作最终断言。
- 时间比较保留语义区别：审计/审批 DATETIME 按 Shanghai 墙钟，schema applicability `effective_from/effective_to` 按 UTC，JSON 内自带时区时间按同一时刻递归比较。该有效时间存储约定是当前对账假设，不应扩展为全部 DB DATETIME 都是 UTC。

## 明确不沿用的错误推论

1. MCP 调用前后整表 count/MAX/update 水位不同，不足以证明调用写库；共享环境其他任务可能正常落库。相同水位也不足以证明无副作用。
2. 请求 future as-of 后出现大于测试开始 MAX 的 ID，不能直接判断未来数据泄漏。PIT 必须依据对应的 available_at/completed_at/published_at，而非本测试进程的读取时间。
3. 公式方言不明确时，AST 无法解析或出现未知名称不自动成为产品缺陷；正式公式用例按有依据的家族契约裁决，不确定项显式阻断。
4. 推荐 items 是因子投影，可以不包含内部 label_code；通过其真实 publication 的 DB eligible route 验证 label 与所选 forecast 的一致性。
5. 仅调用受配额保护的接口并记录一次 quota/timeout，并不构成性能或限流测试。

## 执行与恢复

正常业务执行：

```bash
python3 -m pytest tests/cases/factor4/test_schema_business.py tests/cases/factor4/test_cross_read_business.py --live --env test -m 'not factor4_deferred' -q
```

默认不执行 `factor4_deferred`；只有显式传入 `--include-factor4-deferred` 才启用这些用例。
迁移前来源已保存在 `reports/factor4-script-migration-backup-current/tmp-before-next-migrations.tar.gz`，
SHA256：`606be0874189d3520eb802b6807ce95793dcb3a8ab48b4cec2a3b8f8758fcc20`。
删除前检查所有 Python import 消费者；仍被未删除来源引用的文件须暂时保留。
