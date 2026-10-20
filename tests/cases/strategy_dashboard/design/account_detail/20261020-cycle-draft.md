# 2026-10-20 账户详情：策略历史、分润清算与到账用例初稿

阶段：初步用例设计，未执行。2999 页面现场已见“已分配策略”13列和“历史分润”12列；历史分润为空、无首次 confirmed 到账，因此登记弹窗及分润清算 TAB 为文档条件设计，并非已观察其布局。当前页未见资料编辑或启停按钮，这些动作仅作外部前置，不计为本页字段覆盖。

本文件只补充该子模块，不替换原 264 个检查点。表名来自接口文档模型；实际 DDL、JSON字段、真实消费者和部署版本在执行器接入时校准。结果表与 confirmed 记录可准备读取前置，但公式、出账、到账、权限、幂等需走真实引擎/任务/API。

专用数据：X 的 NT code 为 `qa_ad_cyc_x`，Y 为 `qa_ad_cyc_y`；本地数字 ID 执行时新建/映射，不把2999当可随意覆盖的造数账户。北京时间 UTC+8。普通金额使用Decimal，不从页面舍入值回算。

共用执行：逐条恢复独立前置→写原始输入/关联→确认实际消费及新发布→真实API/任务→独立预期→页面→精确清理。本稿中的“清算周期本金/收益”等为文档语义字段，现场未见的标签待主稿绑定。`待口径绑定`用例有明确不变量，但缺的字段映射不伪装成确定预期。

## 已分配策略（现场可见 13 列）

#### ADC-CYC-001 策略 ID 使用正确策略身份

| 项目 | 设计 |
| --- | --- |
| 字段 | 策略ID |
| 优先级 / 状态 | P1 / 待口径绑定 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | X 关联策略 A：本地 definition=strat_qa_a、NT code=qa_alpha；Y 关联另一策略 B：strat_qa_b/code=qa_beta。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | X 的该行 ID 对应 qa_alpha 的约定显示身份；请求和详情定位同一个 strat_qa_a/qa_alpha，不显示 X 的账户 ID 或 B 的 ID。页面 ID 使用本地 ID 还是 NT code 以 N09 明确字段绑定，不互换写入 ID。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-SEG-001、V2-UI-023 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |
| 待核实/补充 | 现场已确认列名，需把 N09 响应中的具体显示 ID 字段补齐；不把字段缺口判为产品失败。 |

#### ADC-CYC-002 同名策略凭 ID 关联

| 项目 | 设计 |
| --- | --- |
| 字段 | 策略名称 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | A 与 B 均名为“双均线”；X 仅关联 A，Y 仅关联 B。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | X 显示“双均线”，该行关联 A；不因为名称相同混入 B 的费用、收益或详情链接。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-UI-023、V2-SEG-001 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-003 长名称完整识别

| 项目 | 设计 |
| --- | --- |
| 字段 | 策略名称 |
| 优先级 / 状态 | P2 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | A 名称为“账户详情边界测试策略_ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789”。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 名称无乱码；若单元格截断，悬浮或详情可识别完整原文，列宽不覆盖研究员与操作入口；不缩短接口原值。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-UI-023、V2-UI-011 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-004 策略研究员与账户商务分开

| 项目 | 设计 |
| --- | --- |
| 字段 | 研究员 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | A 当前研究员=研究员甲；X 指定商务=商务乙；当前登录人=管理员丙。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 研究员列显示“研究员甲”，不显示“商务乙”或“管理员丙”；研究员变更不把历史收益重新归到别的策略。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-AGG-015、V2-UI-023 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-005 研究员缺失不串入其他人员

| 项目 | 设计 |
| --- | --- |
| 字段 | 研究员 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | A 的研究员字段为空；B 的研究员为研究员乙；X 的商务为商务甲。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | A 的研究员显示当前缺失占位，不以研究员乙或商务甲补齐；该行其他可用字段正常展示。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-UI-012、V2-AGG-015 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-006 来源已确认运行

| 项目 | 设计 |
| --- | --- |
| 字段 | 运行状态 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | A 分配有效；NT 在 2026-10-20 10:00 确认 running，采集该观测已发布。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | A 行显示运行中；保留确认时间/来源证据，不只以 enabled=true 判运行。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-SEG-001、V2-AGG-001 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-007 来源已确认停止

| 项目 | 设计 |
| --- | --- |
| 字段 | 运行状态、结束时间 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | A 在 2026-10-05 12:00 已确认停止；B 在 12:00 后开始运行。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | A 历史行显示已停止/已结束及其真实结束时间；B 当前行运行中，A 历史仍存在。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-SEG-007、V2-SEG-008 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-008 停止命令待确认不提前停机

| 项目 | 设计 |
| --- | --- |
| 字段 | 运行状态 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | 10:00 提交停止请求；当前为排队或超时；10:01 来源仍确认 A running。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 行状态仍基于来源运行证据，不提前显示已停止；运行时间不凭点击时刻截断。仅验证外部动作后的读取，当前页无启停按钮用例。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-SEG-009 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-009 缺心跳与确认停止分开

| 项目 | 设计 |
| --- | --- |
| 字段 | 运行状态 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | 最后有效 running 观测后心跳缺失，未收到 stopped 证据；当前质量为 unknown/stale。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 按契约显示未知/旧状态及必要状态标识，不伪造成确认停止；不生成一个虚构结束时间。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-SEG-010、V2-UI-012 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-010 运行时间列显示真实起始时间

| 项目 | 设计 |
| --- | --- |
| 字段 | 运行时间 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | A 起点=2026-10-01T16:00:00Z（北京时间 10-02 00:00）。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 运行时间列按页面日期格式显示 2026-10-02 对应起点，不把该列当运行天数；时区只转换一次，不显示 10-01 或 10-02 08:00。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-UI-013、V2-SRC-016 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-011 A→B→A 三段历史保留

| 项目 | 设计 |
| --- | --- |
| 字段 | 策略ID、运行时间、结束时间 |
| 优先级 / 状态 | P0 / 待口径绑定 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | A1=[10-01 00:00,10-03 00:00)，B=[10-03 00:00,10-05 00:00)，A2=[10-05 00:00,当前)。三段身份均独立。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 运行历史保留三段及各真实起止，A1/A2 不因策略同 ID 合成一个横跨 B 的连续段；若 UI 按策略汇总，必须仍能追溯三个实际运行段，不能虚构连续运行。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-SEG-002、V2-SEG-021 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |
| 待核实/补充 | 需绑定 N09 当前行组织方式：逐运行段还是策略汇总；三段源证据和不跨 B 插值为固定判定。 |

#### ADC-CYC-012 历史结束时间使用真实切换点

| 项目 | 设计 |
| --- | --- |
| 字段 | 结束时间 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | A 结束=2026-10-03 12:34:56+08:00，B 同刻切入，无确认停机空窗。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | A 结束时间对应 10-03 12:34:56（若 UI 仅日期则为 10-03）；不得用最后采样 12:30 或页面刷新 12:35 替代。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-SEG-004、V2-SEG-006、V2-UI-013 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-013 未结束与缺结束证据不编造

| 项目 | 设计 |
| --- | --- |
| 字段 | 结束时间 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | A 正在运行，effective_to 为空；另独立准备一段已知历史但结束证据缺失的记录。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 运行段未结束显示当前约定占位；历史缺结束证据保留缺失，不填当前时间或下一次采样时间。两种状态各留证据。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-SEG-022、V2-UI-012 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-014 行收益只取本账户本策略段

| 项目 | 设计 |
| --- | --- |
| 字段 | 累计收益率 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | X 的 A 段本金 100、已实现增量 10；X 的 B 段本金 200、增量 40；Y 的 A 段本金 100、增量 30。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | X/A 行累计收益率=10/100=10%，不采用 X 全历史 50/200=25%，也不采用 A 跨账户 40/200=20%。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-AGG-003、V2-SEG-002、V2-MET-006 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-015 负收益保留负号

| 项目 | 设计 |
| --- | --- |
| 字段 | 累计收益率 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | X/A 段本金 100、已实现增量 −5，来源完整。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 行收益率原值 −0.05，页面 −5%，不取绝对值、不按缺失展示。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-AGG-003、V2-UI-010、V2-UI-011 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-016 可用零收益不显示缺失

| 项目 | 设计 |
| --- | --- |
| 字段 | 累计收益率 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | X/A 段本金 100、已实现增量 0，来源完整。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 原值 0，页面 0%（按当前精度），不是“--”；来源缺失另作缺数据。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-AGG-003、V2-UI-012 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-017 同策略内真实加资按资金段计分母

| 项目 | 设计 |
| --- | --- |
| 字段 | 累计收益率 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | X/A 第一段本金 100、收益 10；真实入金 100 后第二段本金 200、收益 40；每段各有 20 张采样。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 行累计收益率=(10+40)/(100+200)=16.666666…%；不按最新本金算 25%，不把 40 张采样本金累加。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-SEG-011、V2-MET-006 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-018 手续费只计对应段一次

| 项目 | 设计 |
| --- | --- |
| 字段 | 累计手续费 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | X/A 段累计成交手续费从 2 增至 3.5；同区间资金费支出 0.8；X/B 手续费增量 4。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | X/A 累计手续费=1.5 USDT；不计为累计末值 3.5，不混入 B 的 4 或资金费 0.8。展示方向按当前手续费金额契约。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-SRC-005、V2-SRC-008、V2-AGG-004 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-019 手续费计数器重置分周期取差

| 项目 | 设计 |
| --- | --- |
| 字段 | 累计手续费 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | A 周期 1 的手续费累计 10→12；周期 2 从 0→3；第二周期真实新运行实例，不是负费用。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 同一行适用范围的手续费=2+3=5；不得相减为 −7，也不能把 12 与 3 直接相加为 15。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-SRC-009、V2-SRC-010 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-020 不足七天不借用账户历史

| 项目 | 设计 |
| --- | --- |
| 字段 | 年化收益率 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | X 账号已运行 30 天，当前 A 段有效运行 6 天 23:59:59、收益率 10%。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 该行年化收益率不可用，明确属于不足 7 天；不借 X 的 30 天或其他策略时长算数。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-MET-009、V2-UI-012 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-021 恰满七天开始简单年化

| 项目 | 设计 |
| --- | --- |
| 字段 | 年化收益率 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | A 行收益率=7%，有效运行时长恰 7 天（604800 秒），证据完整。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 年化=0.07/7×365=3.65，页面 365%；无需等到第 8 天，不使用复利年化。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-MET-010、V2-UI-010 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-022 超过七天使用实际时长

| 项目 | 设计 |
| --- | --- |
| 字段 | 年化收益率 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | A 行收益率=10%，有效运行=10 天；其他数据与七天例独立。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 年化=0.10/10×365=3.65，页面 365%；不得固定用 7 天得到 521.428571%。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-MET-011 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-023 真实停止空窗不加进策略有效时长

| 项目 | 设计 |
| --- | --- |
| 字段 | 年化收益率、运行时间、结束时间 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | A 实际运行 10-01 至 10-05 共 4 天；确认停机 10-05 至 10-10 共 5 天；再运行 10-10 至 10-14 共 4 天；合并行收益率=8%。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 有效运行 8 天，简单年化=0.08/8×365=365%；不按日历跨度 13 天得 224.6153846%。起止字段仍保留实际段证据。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-SEG-007、V2-SEG-020、V2-MET-011 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |
| 待核实/补充 | 若 UI 逐运行段展示，分两行核对各自；此计算断言在相同 A 汇总范围执行，不强制改变当前 UI 行组织。 |

#### ADC-CYC-024 分钟峰谷不被日显示抹掉

| 项目 | 设计 |
| --- | --- |
| 字段 | 最大回撤 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | A 行适用范围本金固定 1000、未实现为 0；同日四个真实分钟的累计已实现为 0、200、−100、100，对应净值 1、1.2、0.9、1.1。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 最大回撤=(1.2−0.9)/1.2=25%；不把末日净值 1.1 当唯一输入得 0，不显示实时回撤 8.333333% 替代最大回撤。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-RISK-002、V2-UI-023 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-025 无有效净值样本与零回撤分开

| 项目 | 设计 |
| --- | --- |
| 字段 | 最大回撤 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | A 的本金有效但所有分钟已实现输入缺失；B 有真实净值 1→1.1→1.2。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | A 最大回撤不可用，B 最大回撤为真实 0%；不把 A 显示为 0%。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-UI-012、V2-RISK-004、V2-RISK-007 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-026 行夏普使用行范围输入

| 项目 | 设计 |
| --- | --- |
| 字段 | 夏普比率 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | A 连续 10 个完整日的日收益依次为 +1%、−1% 重复 5 次；本金固定 1000；行总收益率 0、有效运行 10 天；合法且新鲜的配置年无风险利率明确设为 0。另 B 有正收益。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | A 的日标准差非零，分子为 0，日夏普及年化夏普均为 0；不混入 B 收益或账户全历史。配置 0 是有效测试输入，不能用其模拟缺失利率默认值。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations；合法利率配置/缓存（实际入口接入时绑定） |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-RISK-021、V2-RISK-022、V2-RISK-028、V2-AGG-003 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-027 样本不足只影响相关指标

| 项目 | 设计 |
| --- | --- |
| 字段 | 夏普比率 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | A 仅有 1 个完整日样本，收益率 1%；真实有效运行也仅 1 天，其他身份/累计手续费可用。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 夏普不可用，不强行 std=0 后给 0；手续费、名称、状态正常显示；年化另按不足 7 天不可用。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-RISK-019、V2-UI-012 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-028 卡玛由同行年化除同期最大回撤

| 项目 | 设计 |
| --- | --- |
| 字段 | 卡玛比率 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | A 行 10 天收益率 10%，年化 3.65；同范围分钟最大回撤 0.25，输入可控且与账户其他策略隔离。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 卡玛=3.65/0.25=14.6；不平均其他账户/策略卡玛，不以实时回撤做分母。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-RISK-032、V2-AGG-007 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-029 零回撤不显示无穷大

| 项目 | 设计 |
| --- | --- |
| 字段 | 卡玛比率 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | A 满 10 天，收益率 10%、年化 3.65，净值单调上升，最大回撤 0。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 卡玛不可用，不能输出 Infinity、NaN 或 0 冒充可计算；年化 365% 和最大回撤 0% 仍可分别显示。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-RISK-033、V2-UI-012 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-030 策略详情链接定位正确

| 项目 | 设计 |
| --- | --- |
| 字段 | 操作 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | A 的本地定义 ID=strat_qa_a、NT code=qa_alpha；同表存在 B；登录人有 A 访问权。 |
| 步骤 | 1. 准备 A/B 两个有区分的合法策略身份。<br>2. 打开 X 的 A 行，记录链接并实际点击。<br>3. 核对目标策略标题、ID；返回 X。 |
| 明确预期 | 点击 A 行“策略详情”进入 A，而非 B 或账户 X；链接带正确可识别策略身份，返回账户详情仍是 X。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-UI-009、V2-UI-023 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-031 已删除策略保留历史且不错误跳转

| 项目 | 设计 |
| --- | --- |
| 字段 | 策略ID、策略名称、操作 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | X 历史运行 A 已结束且 A 定义被删除/失效；B 为当前策略，身份不同。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | A 历史行保留可追踪身份与已知历史字段；不跳转 B 或无关策略；不可访问入口按当前契约禁用/提示。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-SEG-022、V2-UI-022 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-032 不可访问策略详情保留原权限

| 项目 | 设计 |
| --- | --- |
| 字段 | 操作 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | X 详情可访问，历史策略 A 的独立详情不在用户可见范围。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 从 X 的历史行无法越权读取 A 详情；账户历史事实不被整行错误删除，不因隐藏链接而放行直接请求。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-UI-021、V2-UI-022 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-033 已分配策略十一条独立分页

| 项目 | 设计 |
| --- | --- |
| 字段 | 策略ID、运行时间 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | X 有 11 段顺序身份 R01…R11，各合法单策略串行；历史分润另有 3 条。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 策略表每页 10 条，第二页 1 条；11 段各出现一次。翻策略页不翻历史分润页、不改变收益卡片范围。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-UI-004、V2-AGG-009 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

#### ADC-CYC-034 无策略历史时完整空表

| 项目 | 设计 |
| --- | --- |
| 字段 | 策略ID、策略名称、研究员、运行状态、运行时间、结束时间、累计收益率、累计手续费、年化收益率、最大回撤、夏普比率、卡玛比率、操作 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | X 有合法账户详情，但无任何分配/运行段，真实返回空集合且查询成功。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 已分配策略表为空态；无伪造默认策略/零收益行；若总数可确定为 0 则显示 0。查询失败不能冒充此空态。 |
| DB入口 | quant_accounts；quant_account_profiles；quant_strategy_definitions；quant_strategy_account_runs；account_strategy_assignment；account_strategy_migration；fact_performance_sample；fact_nav_sample；quant_segmented_performance_generations |
| 真实API/处理 | N09 账户运行/关联策略查询；N01 指标（按行范围）；必要时 E07 账户详情。具体路由沿用接口文档，不另造重算接口。 |
| 检查点 | V2-UI-004、V2-UI-012 |
| 规则依据 | T K1/K2、Q1—Q7；20261020-coverage-matrix.md 对应检查点。 |

## 历史分润（现场可见 12 列；2999 当前为空）

#### ADC-CYC-035 账单账户字段绑定 NT 身份

| 项目 | 设计 |
| --- | --- |
| 字段 | 账号ID |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | 账单 B1 account_id=X 的本地数字 ID，映射 NT code=qa_ad_cyc_x；Y 的另一张账单 NT code=qa_ad_cyc_y。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | X 的 B1 行账号 ID 显示与页头一致的 qa_ad_cyc_x，不能显示 Y 或把本地数字 ID 当 NT 代码；账单写操作仍使用对应真实本地资源 ID。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N21/N22 账户账单与到账查询；正式到账走 N23，出账由独立计费任务；具体字段/路由以当前接口文档绑定。 |
| 检查点 | V2-UI-023、V2-UI-021 |
| 规则依据 | T L1、M1/M2、7.1—7.4；P 最新分润说明与确认 7/9/10。 |

#### ADC-CYC-036 已出账费率冻结

| 项目 | 设计 |
| --- | --- |
| 字段 | 分润比例 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1 触发时比例 35%、合格利润 5，应收 1.75；之后外部配置改为 50%。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | B1 分润比例仍 35%、应收仍 1.75；新政策不回写旧单。后续新单按其有效政策取值。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N21/N22 账户账单与到账查询；正式到账走 N23，出账由独立计费任务；具体字段/路由以当前接口文档绑定。 |
| 检查点 | V2-BILL-020、V2-BILL-018 |
| 规则依据 | T L1、M1/M2、7.1—7.4；P 最新分润说明与确认 7/9/10。 |

#### ADC-CYC-037 触发时间与到账时间分开

| 项目 | 设计 |
| --- | --- |
| 字段 | 触发分润时间 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1 trigger_at=2026-10-01 10:00，首次实际到账=2026-10-03 16:00。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 触发分润时间列固定为 10-01 10:00，不显示到账 10-03 16:00 或页面刷新时间；页面清算基准也以 trigger_at 为准。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N21/N22 账户账单与到账查询；正式到账走 N23，出账由独立计费任务；具体字段/路由以当前接口文档绑定。 |
| 检查点 | V2-REC-019、V2-UI-013 |
| 规则依据 | T L1、M1/M2、7.1—7.4；P 最新分润说明与确认 7/9/10。 |

#### ADC-CYC-038 分润前钱包使用触发快照

| 项目 | 设计 |
| --- | --- |
| 字段 | 钱包余额（分润前） |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1 触发精确钱包 105；之后新周期赚 3，当前钱包 108；无真实资金划转。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | B1 分润前钱包固定为 105 USDT，不随实时钱包改为 108，也不是扣应收后的 103.25。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N21/N22 账户账单与到账查询；正式到账走 N23，出账由独立计费任务；具体字段/路由以当前接口文档绑定。 |
| 检查点 | V2-BILL-014、V2-BILL-018 |
| 规则依据 | T L1、M1/M2、7.1—7.4；P 最新分润说明与确认 7/9/10。 |

#### ADC-CYC-039 分润前利润冻结且不混账户全历史

| 项目 | 设计 |
| --- | --- |
| 字段 | 已实现收益（分润前） |
| 优先级 / 状态 | P0 / 待口径绑定 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | 账户全历史已实现 25，但 B1 可计费周期利润 5；触发本金 100、钱包 105。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | B1 触发利润/已实现收益（分润前）按账单冻结语义取 5，不用账户全历史 25 计算应收；若 UI 同名字段实际定义为源累计已实现，则应明确展示 25 并与周期利润字段分开，不能混用。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N21/N22 账户账单与到账查询；正式到账走 N23，出账由独立计费任务；具体字段/路由以当前接口文档绑定。 |
| 检查点 | V2-BILL-014、V2-BILL-018 |
| 规则依据 | T L1、M1/M2、7.1—7.4；P 最新分润说明与确认 7/9/10。 |
| 待核实/补充 | 空表现场无法确认“已实现收益（分润前）”绑定周期利润还是源累计已实现；两者取不同值的样本用于锁定，收费公式仍是周期合格利润×冻结费率。 |

#### ADC-CYC-040 恰达门槛的应收

| 项目 | 设计 |
| --- | --- |
| 字段 | 应收分润金额 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | 有效本金 100、门槛 5%、合格利润 5、钱包 105、分润比例 35%；精确源新鲜完整。 |
| 步骤 | 1. 准备 policy/cycle 与精确源，不直接写应收结果。<br>2. 让正式计费任务消费并读取 B1。<br>3. 核对 1.75 应收、触发引用及后继周期，再看表格。 |
| 明确预期 | 真实检测生成 B1 一张账单，应收=5×35%=1.75 USDT；前后金额字段关联同一触发快照，新周期本金 105。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N21/N22 账户账单与到账查询；正式到账走 N23，出账由独立计费任务；具体字段/路由以当前接口文档绑定。 |
| 检查点 | V2-BILL-014、V2-BILL-021 |
| 规则依据 | T L1、M1/M2、7.1—7.4；P 最新分润说明与确认 7/9/10。 |

#### ADC-CYC-041 一次跨过多档只按实际利润出一单

| 项目 | 设计 |
| --- | --- |
| 字段 | 应收分润金额 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | 本金 100、门槛 5%、利润首次观察为 20、钱包 120、分润比例 35%。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 一张应收 7 USDT 的账单，不能拆成四张 1.75，也不能仅对超额 15 收取 5.25。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N21/N22 账户账单与到账查询；正式到账走 N23，出账由独立计费任务；具体字段/路由以当前接口文档绑定。 |
| 检查点 | V2-BILL-017 |
| 规则依据 | T L1、M1/M2、7.1—7.4；P 最新分润说明与确认 7/9/10。 |

#### ADC-CYC-042 未登记到账不伪造实收零记录

| 项目 | 设计 |
| --- | --- |
| 字段 | 分润到账时间、实收分润金额 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1 应收 1.75，尚无 confirmed receipt，账单已正式生成。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 确认到账金额缺失按当前占位展示；没有实际到账时间，不把触发时间复制为到账；后端未收为 1.75，清算 TAB 仍隐藏。已知空收款集合不等于已确认 0 元收款。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N21/N22 账户账单与到账查询；正式到账走 N23，出账由独立计费任务；具体字段/路由以当前接口文档绑定。 |
| 检查点 | V2-AGG-014、V2-REC-018、V2-UI-012 |
| 规则依据 | T L1、M1/M2、7.1—7.4；P 最新分润说明与确认 7/9/10。 |

#### ADC-CYC-043 单次到账保留实际发生时刻

| 项目 | 设计 |
| --- | --- |
| 字段 | 分润到账时间 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1 于 10-01 10:00 触发；N23 在 10-04 18:00 操作补录实际到账 2026-10-03T08:00:00Z。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 到账时间为北京时间 10-03 16:00，非操作时间 10-04 18:00、非触发时间；原绝对时刻不变。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N21/N22 账户账单与到账查询；正式到账走 N23，出账由独立计费任务；具体字段/路由以当前接口文档绑定。 |
| 检查点 | V2-REC-015、V2-UI-013 |
| 规则依据 | T L1、M1/M2、7.1—7.4；P 最新分润说明与确认 7/9/10。 |

#### ADC-CYC-044 多次到账保留每笔时间和累计金额

| 项目 | 设计 |
| --- | --- |
| 字段 | 分润到账时间、实收分润金额 |
| 优先级 / 状态 | P1 / 待口径绑定 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1 应收 1.75；receipt R1=0.75，发生 10-03 16:00；R2=1.00，发生 10-04 11:00。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 累计 confirmed 实收 1.75、待收 0；详情可追踪 R1/R2 各自实际时间。主表一格时间若取最新，应为 10-04 11:00；若另有已确认聚合语义则明确绑定，不能用操作时间或丢掉两笔事实。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N21/N22 账户账单与到账查询；正式到账走 N23，出账由独立计费任务；具体字段/路由以当前接口文档绑定。 |
| 检查点 | V2-REC-010、V2-REC-011、V2-REC-015 |
| 规则依据 | T L1、M1/M2、7.1—7.4；P 最新分润说明与确认 7/9/10。 |
| 待核实/补充 | 主表多次到账时单格“分润到账时间”的展示选择尚未从空表验证，执行前绑定具体 API 字段。 |

#### ADC-CYC-045 登记不合成扣钱包结果

| 项目 | 设计 |
| --- | --- |
| 字段 | 钱包余额（分润后） |
| 优先级 / 状态 | P0 / 待口径绑定 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1 触发钱包 105、应收 1.75；之后钱包实际仍 105，没有资金流；指定商务登记 1.75。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 登记前后真实钱包均 105；不能因登记直接显示合成的 103.25。分润后钱包字段须绑定实际 after_snapshot/ref：若存在对应 105 证据显示 105，若无证据则缺失，不用 105−1.75 推算。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N21/N22 账户账单与到账查询；正式到账走 N23，出账由独立计费任务；具体字段/路由以当前接口文档绑定。 |
| 检查点 | V2-REC-007、V2-REC-008、V2-UI-012 |
| 规则依据 | T L1、M1/M2、7.1—7.4；P 最新分润说明与确认 7/9/10。 |
| 待核实/补充 | 固定业务不变量已确认；after 字段取触发后、登记时还是独立 after snapshot 需绑定 N21/N22 实际契约。 |

#### ADC-CYC-046 登记不把后继利润清零

| 项目 | 设计 |
| --- | --- |
| 字段 | 已实现收益（分润后） |
| 优先级 / 状态 | P0 / 待口径绑定 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1 触发钱包 105；新周期无流转又赚 3，钱包 108；随后登记 B1 的 1.75。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 新周期利润登记后仍为 3；不能登记后显示 0 并丢失该利润。分润后已实现字段若绑定周期利润则为 3；若绑定源累计则按真实源累计，不由测试擅自重置。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N21/N22 账户账单与到账查询；正式到账走 N23，出账由独立计费任务；具体字段/路由以当前接口文档绑定。 |
| 检查点 | V2-REC-007、V2-REC-022 |
| 规则依据 | T L1、M1/M2、7.1—7.4；P 最新分润说明与确认 7/9/10。 |
| 待核实/补充 | 需明确该表列绑定 frozen after snapshot、实时周期利润还是源累计；此处不改变主公式。 |

#### ADC-CYC-047 首次部分到账只增加原单实收

| 项目 | 设计 |
| --- | --- |
| 字段 | 实收分润金额 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1 应收 1.75，N23 正式确认 R1=0.75；B2 另有应收 2.80 尚未收。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | B1 实收 0.75、待收 1.00；B2 实收仍缺失、待收 2.80；不能把 0.75 同时计入两个账单。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N21/N22 账户账单与到账查询；正式到账走 N23，出账由独立计费任务；具体字段/路由以当前接口文档绑定。 |
| 检查点 | V2-REC-010 |
| 规则依据 | T L1、M1/M2、7.1—7.4；P 最新分润说明与确认 7/9/10。 |

#### ADC-CYC-048 部分再补足准确累计

| 项目 | 设计 |
| --- | --- |
| 字段 | 实收分润金额 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1 已 confirmed 0.75，最新 bill_version 有效；再次正式登记 1.00。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | B1 实收=1.75、待收=0，两笔各一次；不显示最后一笔 1.00 取代累计，不计成 2.50。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N21/N22 账户账单与到账查询；正式到账走 N23，出账由独立计费任务；具体字段/路由以当前接口文档绑定。 |
| 检查点 | V2-REC-011 |
| 规则依据 | T L1、M1/M2、7.1—7.4；P 最新分润说明与确认 7/9/10。 |

#### ADC-CYC-049 商务与研究员和操作者身份区分

| 项目 | 设计 |
| --- | --- |
| 字段 | 商务 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | X 当前指定商务=商务甲；策略研究员=研究员乙；B1 操作记录为商务甲。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | B1 商务列指向约定的账户/账单商务甲，不显示研究员乙或当前登录用户名；登记审计保留商务甲的真实身份。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N21/N22 账户账单与到账查询；正式到账走 N23，出账由独立计费任务；具体字段/路由以当前接口文档绑定。 |
| 检查点 | V2-REC-016、V2-UI-019 |
| 规则依据 | T L1、M1/M2、7.1—7.4；P 最新分润说明与确认 7/9/10。 |

#### ADC-CYC-050 更换商务后按当前指定商务授权

| 项目 | 设计 |
| --- | --- |
| 字段 | 商务、操作 |
| 优先级 / 状态 | P0 / 待口径绑定 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1 未收；X 当前商务由甲改为乙，版本已生效；分别由甲和乙打开/直调登记。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 乙符合其他权限时可登记；甲不再因历史曾负责而放行。历史主表“商务”显示冻结负责人还是当前负责人须绑定契约，但不能据此绕过当前指定商务校验。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N21/N22 账户账单与到账查询；正式到账走 N23，出账由独立计费任务；具体字段/路由以当前接口文档绑定。 |
| 检查点 | V2-REC-016、V2-REC-017 |
| 规则依据 | T L1、M1/M2、7.1—7.4；P 最新分润说明与确认 7/9/10。 |
| 待核实/补充 | 写入权限固定按当前指定商务；主表商务列的历史/当前字段语义需 N21/N22 对应字段确认。 |

#### ADC-CYC-051 仍有待收时指定商务可登记

| 项目 | 设计 |
| --- | --- |
| 字段 | 操作 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1 应收 1.75、已收 0.75，当前指定商务登录且账号可访问，最新 bill_version。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 操作区提供当前确认流程的登记入口，打开原表单；本次可登记上限 1.00，不能让按钮切到 B2。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N21/N22 账户账单与到账查询；正式到账走 N23，出账由独立计费任务；具体字段/路由以当前接口文档绑定。 |
| 检查点 | V2-UI-014、V2-UI-019、V2-REC-011 |
| 规则依据 | T L1、M1/M2、7.1—7.4；P 最新分润说明与确认 7/9/10。 |

#### ADC-CYC-052 已收足不继续登记

| 项目 | 设计 |
| --- | --- |
| 字段 | 操作 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1 应收=实收=1.75，余额 0。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 登记入口按当前规则隐藏/禁用；直接提交正金额被拒且无新 receipt；查看详情仍可读取。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N21/N22 账户账单与到账查询；正式到账走 N23，出账由独立计费任务；具体字段/路由以当前接口文档绑定。 |
| 检查点 | V2-REC-011、V2-REC-012、V2-REC-027 |
| 规则依据 | T L1、M1/M2、7.1—7.4；P 最新分润说明与确认 7/9/10。 |

#### ADC-CYC-053 历史分润十一条独立分页

| 项目 | 设计 |
| --- | --- |
| 字段 | 账号ID、触发分润时间、应收分润金额 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | X 有 B01…B11 共 11 张合法账单，触发时间唯一；策略表 3 行，固定第一页。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 历史分润默认每页 10，第二页 1；账单身份无重复/遗漏。翻历史分润不改变策略页与卡片对象；新发布并分页时保留 snapshot/筛选条件。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N21/N22 账户账单与到账查询；正式到账走 N23，出账由独立计费任务；具体字段/路由以当前接口文档绑定。 |
| 检查点 | V2-UI-004、V2-AGG-009 |
| 规则依据 | T L1、M1/M2、7.1—7.4；P 最新分润说明与确认 7/9/10。 |

#### ADC-CYC-054 历史分润查询失败不显示成功空表

| 项目 | 设计 |
| --- | --- |
| 字段 | 应收分润金额、实收分润金额、操作 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | X 有 B1 应收 1.75；受控令账单查询失败，策略和主卡请求仍成功。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 历史分润局部错误/重试状态，不能显示“暂无数据”并把应收当 0；其他模块保持可用且不串入旧账户账单。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N21/N22 账户账单与到账查询；正式到账走 N23，出账由独立计费任务；具体字段/路由以当前接口文档绑定。 |
| 检查点 | V2-UI-012、V2-UI-009 |
| 规则依据 | T L1、M1/M2、7.1—7.4；P 最新分润说明与确认 7/9/10。 |

## 分润清算 TAB 与周期基准（条件设计，现场未出现）

#### ADC-CYC-055 从未到账时隐藏 TAB

| 项目 | 设计 |
| --- | --- |
| 字段 | 分润清算TAB |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | X 无任何 confirmed receipt，可能有完整策略历史和账户指标。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 仅真实净值数据 TAB，可读全历史；不显示清算 TAB；不能因账户盈利、已配置政策或有运行策略提前出现。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N01/N02 账户清算范围指标/曲线；N21/N22 账单和到账；N23 正式登记。范围参数以接口文档为准。 |
| 检查点 | V2-REC-018、V2-UI-023 |
| 规则依据 | T J0/J1、Q8、7.1—7.4；P 首次到账显示 TAB 与最新确认账单触发起点。 |

#### ADC-CYC-056 首次出账未收时后台前进但 TAB 隐藏

| 项目 | 设计 |
| --- | --- |
| 字段 | 分润清算TAB |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | T1=10-01 10:00 触发 B1：本金100、钱包105、应收1.75；尚无 confirmed receipt。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 后台后继周期 C2 已从 T1 开始且本金105；页面清算TAB仍隐藏。不能为了隐藏 TAB 延迟后台切周期，也不能仅出账就显示 TAB。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N01/N02 账户清算范围指标/曲线；N21/N22 账单和到账；N23 正式登记。范围参数以接口文档为准。 |
| 检查点 | V2-BILL-021、V2-REC-018 |
| 规则依据 | T J0/J1、Q8、7.1—7.4；P 首次到账显示 TAB 与最新确认账单触发起点。 |

#### ADC-CYC-057 首次部分到账即显示 TAB

| 项目 | 设计 |
| --- | --- |
| 字段 | 分润清算TAB、清算统计起点 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1 于 T1=10-01 10:00 触发；10-03 16:00 首次 confirmed 0.75/应收1.75。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | TAB 显示；统计起点=T1，而非10-03到账时间；不等收足才显示。B1 待收仍1.00。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N01/N02 账户清算范围指标/曲线；N21/N22 账单和到账；N23 正式登记。范围参数以接口文档为准。 |
| 检查点 | V2-REC-010、V2-REC-019 |
| 规则依据 | T J0/J1、Q8、7.1—7.4；P 首次到账显示 TAB 与最新确认账单触发起点。 |

#### ADC-CYC-058 首次一次收足同样使用触发点

| 项目 | 设计 |
| --- | --- |
| 字段 | 分润清算TAB、清算统计起点 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1 T1=10-01 10:00；10-03 16:00 首次 confirmed 1.75。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | TAB 显示、起点 T1；待收0。此分支独立于首次部分到账，不能把第一次确认当到账时清零。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N01/N02 账户清算范围指标/曲线；N21/N22 账单和到账；N23 正式登记。范围参数以接口文档为准。 |
| 检查点 | V2-REC-011、V2-REC-019 |
| 规则依据 | T J0/J1、Q8、7.1—7.4；P 首次到账显示 TAB 与最新确认账单触发起点。 |

#### ADC-CYC-059 新未收账单不前移页面基准

| 项目 | 设计 |
| --- | --- |
| 字段 | 清算统计起点、清算累计收益率 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1 trigger T1、本金100、触发钱包105且已确认部分到账；B2 T2=10-10 10:00触发钱包113、应收2.80但未收；当前钱包116，无资金流。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 后台最新周期起点T2、本金113、利润3；页面仍从T1按其有效本金105、期间利润11计算，收益率11/105≈10.476190%。两种范围并存，不能让新未收B2将页面起点切T2。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N01/N02 账户清算范围指标/曲线；N21/N22 账单和到账；N23 正式登记。范围参数以接口文档为准。 |
| 检查点 | V2-REC-020、V2-BILL-024 |
| 规则依据 | T J0/J1、Q8、7.1—7.4；P 首次到账显示 TAB 与最新确认账单触发起点。 |

#### ADC-CYC-060 最新账单首次确认后页面推进

| 项目 | 设计 |
| --- | --- |
| 字段 | 清算统计起点、清算累计收益率 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | 沿上一条；B2 在10-12 首次确认0.80，当前钱包仍116，无资金流。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 清算起点推进到 T2；有效本金113、利润3、收益率3/113≈2.654867%；B1历史实收不丢，B2剩余2.00。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N01/N02 账户清算范围指标/曲线；N21/N22 账单和到账；N23 正式登记。范围参数以接口文档为准。 |
| 检查点 | V2-REC-022、V2-REC-010 |
| 规则依据 | T J0/J1、Q8、7.1—7.4；P 首次到账显示 TAB 与最新确认账单触发起点。 |

#### ADC-CYC-061 旧账单晚补录不倒退

| 项目 | 设计 |
| --- | --- |
| 字段 | 清算统计起点、实收分润金额 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1 T1，已收0.75；B2 T2且已在10-12确认0.80，页面起点T2；10-13再给B1补1.00。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | B1实收1.75、B2实收0.80；页面仍从T2，不退回T1；当前钱包/新周期利润不因旧款登记改变。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N01/N02 账户清算范围指标/曲线；N21/N22 账单和到账；N23 正式登记。范围参数以接口文档为准。 |
| 检查点 | V2-REC-021 |
| 规则依据 | T J0/J1、Q8、7.1—7.4；P 首次到账显示 TAB 与最新确认账单触发起点。 |

#### ADC-CYC-062 触发后无流转无盈亏

| 项目 | 设计 |
| --- | --- |
| 字段 | 清算周期本金、清算已实现收益、清算累计收益率 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | T1 钱包105；后续钱包105，无真实入出金，也没有新损益。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 后继周期有效本金105，利润0，收益率0%；可用0与缺数据区分。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N01/N02 账户清算范围指标/曲线；N21/N22 账单和到账；N23 正式登记。范围参数以接口文档为准。 |
| 检查点 | V2-REC-001 |
| 规则依据 | T J0/J1、Q8、7.1—7.4；P 首次到账显示 TAB 与最新确认账单触发起点。 |

#### ADC-CYC-063 触发后真实转出分润金额

| 项目 | 设计 |
| --- | --- |
| 字段 | 清算周期本金、清算已实现收益、清算累计收益率 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | T1 钱包105；真实出金F1=1.75，当前钱包103.25，无新损益。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 本金=105−1.75=103.25；利润0、收益率0%。出金只改有效本金与钱包，不当−1.75交易亏损。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N01/N02 账户清算范围指标/曲线；N21/N22 账单和到账；N23 正式登记。范围参数以接口文档为准。 |
| 检查点 | V2-REC-002 |
| 规则依据 | T J0/J1、Q8、7.1—7.4；P 首次到账显示 TAB 与最新确认账单触发起点。 |

#### ADC-CYC-064 触发后提走全部旧盈利

| 项目 | 设计 |
| --- | --- |
| 字段 | 清算周期本金、清算已实现收益、清算累计收益率 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | T1 钱包105；真实转出5，当前钱包100，无新损益。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 有效本金100、利润0、收益率0%；不能把已计费的旧盈利5重新当本周期利润。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N01/N02 账户清算范围指标/曲线；N21/N22 账单和到账；N23 正式登记。范围参数以接口文档为准。 |
| 检查点 | V2-REC-003 |
| 规则依据 | T J0/J1、Q8、7.1—7.4；P 首次到账显示 TAB 与最新确认账单触发起点。 |

#### ADC-CYC-065 出金后再盈利

| 项目 | 设计 |
| --- | --- |
| 字段 | 清算周期本金、清算已实现收益、清算累计收益率 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | T1 钱包105；真实出1.75，本期又赚1，当前钱包104.25。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 有效本金103.25，利润1，收益率1/103.25≈0.968523%；登记到账后仍保持这些值。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N01/N02 账户清算范围指标/曲线；N21/N22 账单和到账；N23 正式登记。范围参数以接口文档为准。 |
| 检查点 | V2-REC-004、V2-REC-007 |
| 规则依据 | T J0/J1、Q8、7.1—7.4；P 首次到账显示 TAB 与最新确认账单触发起点。 |

#### ADC-CYC-066 出金后发生亏损

| 项目 | 设计 |
| --- | --- |
| 字段 | 清算周期本金、清算已实现收益、清算累计收益率 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | T1 钱包105；真实出1.75，本期亏1，当前钱包102.25。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 有效本金103.25，利润−1，收益率−1/103.25≈−0.968523%；不取绝对值，不把登记当归零。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N01/N02 账户清算范围指标/曲线；N21/N22 账单和到账；N23 正式登记。范围参数以接口文档为准。 |
| 检查点 | V2-REC-005、V2-REC-007 |
| 规则依据 | T J0/J1、Q8、7.1—7.4；P 首次到账显示 TAB 与最新确认账单触发起点。 |

#### ADC-CYC-067 追加资本与出金同时影响本期本金

| 项目 | 设计 |
| --- | --- |
| 字段 | 清算周期本金、清算已实现收益、清算累计收益率 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | T1钱包105；真实入20、真实出1.75，本期赚1，当前钱包124.25。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 本金=105+20−1.75=123.25；利润1，收益率1/123.25≈0.811359%；20入金不计成盈利。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N01/N02 账户清算范围指标/曲线；N21/N22 账单和到账；N23 正式登记。范围参数以接口文档为准。 |
| 检查点 | V2-REC-006、V2-REC-007 |
| 规则依据 | T J0/J1、Q8、7.1—7.4；P 首次到账显示 TAB 与最新确认账单触发起点。 |

#### ADC-CYC-068 从其他账户付款不合成资管出金

| 项目 | 设计 |
| --- | --- |
| 字段 | 清算已实现收益、钱包余额（分润后） |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | 资管X触发钱包105后又赚3至108；商务从其他账户付1.75并登记，X无真实资金流。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | X钱包仍108、本期本金105、利润3；不合成F1=−1.75，不显示106.25或利润1.25。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N01/N02 账户清算范围指标/曲线；N21/N22 账单和到账；N23 正式登记。范围参数以接口文档为准。 |
| 检查点 | V2-REC-008 |
| 规则依据 | T J0/J1、Q8、7.1—7.4；P 首次到账显示 TAB 与最新确认账单触发起点。 |

#### ADC-CYC-069 真实出金与登记不重复计流

| 项目 | 设计 |
| --- | --- |
| 字段 | 清算周期本金、清算已实现收益 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | X真实出1.75已形成唯一资金流F1；此时钱包104.25、本金103.25、利润1；随后N23登记同金额1.75。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | F1仅一次；登记后钱包104.25、本金103.25、利润1；不能再次减1.75。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N01/N02 账户清算范围指标/曲线；N21/N22 账单和到账；N23 正式登记。范围参数以接口文档为准。 |
| 检查点 | V2-REC-009 |
| 规则依据 | T J0/J1、Q8、7.1—7.4；P 首次到账显示 TAB 与最新确认账单触发起点。 |

#### ADC-CYC-070 账户老历史不放宽新周期七天门槛

| 项目 | 设计 |
| --- | --- |
| 字段 | 清算年化收益率、清算卡玛比率 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | 账号已运行30天；最新已确认账单触发至当前仅6天23:59:59；清算收益率10%、有效最大回撤25%。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 清算年化及卡玛不可用；真实净值数据TAB的全历史指标按各自范围保持可用，不借30天给清算年化。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N01/N02 账户清算范围指标/曲线；N21/N22 账单和到账；N23 正式登记。范围参数以接口文档为准。 |
| 检查点 | V2-REC-029、V2-MET-009 |
| 规则依据 | T J0/J1、Q8、7.1—7.4；P 首次到账显示 TAB 与最新确认账单触发起点。 |

#### ADC-CYC-071 清算恰满七天

| 项目 | 设计 |
| --- | --- |
| 字段 | 清算年化收益率、清算卡玛比率 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | 最新已确认触发点距当前有效运行恰7天；清算收益率7%、清算最大回撤25%。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 年化0.07/7×365=3.65（365%）；卡玛3.65/0.25=14.6；不多等一天，不混全历史回撤。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N01/N02 账户清算范围指标/曲线；N21/N22 账单和到账；N23 正式登记。范围参数以接口文档为准。 |
| 检查点 | V2-REC-029、V2-RISK-032、V2-MET-010 |
| 规则依据 | T J0/J1、Q8、7.1—7.4；P 首次到账显示 TAB 与最新确认账单触发起点。 |

#### ADC-CYC-072 新周期样本不足不借历史样本

| 项目 | 设计 |
| --- | --- |
| 字段 | 清算夏普比率、清算最大回撤 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | 旧周期有100个完整日；新清算范围仅1个完整日，只有1个有效净值点。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 新范围夏普和不能计算的回撤按自身不可用状态展示；不拼旧周期样本以生成看似完整结果；其他新周期可用金额正常。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N01/N02 账户清算范围指标/曲线；N21/N22 账单和到账；N23 正式登记。范围参数以接口文档为准。 |
| 检查点 | V2-REC-030、V2-UI-012 |
| 规则依据 | T J0/J1、Q8、7.1—7.4；P 首次到账显示 TAB 与最新确认账单触发起点。 |

#### ADC-CYC-073 跨周期未平仓保留真实成本

| 项目 | 设计 |
| --- | --- |
| 字段 | 清算交易统计 |
| 优先级 / 状态 | P1 / 待口径绑定 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | T1前以100买1，手续费0.1，T1后以110卖1，手续费0.1，恰形成完整平仓轮。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 完整成本证据100及两次实际成交手续费0.2可追踪，轮次净利润9.8；清算范围轮次是否纳入按当前Q6/J1事件归属绑定，不能在T1切断成本后错误记109.9利润。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows；fact_fill |
| 真实API/处理 | N01/N02 账户清算范围指标/曲线；N21/N22 账单和到账；N23 正式登记。范围参数以接口文档为准。 |
| 检查点 | V2-REC-031、V2-TRD-001、V2-TRD-004 |
| 规则依据 | T J0/J1、Q8、7.1—7.4；P 首次到账显示 TAB 与最新确认账单触发起点。 |
| 待核实/补充 | 固定轮次净利润9.8；区间胜率/轮次分母何时计入必须绑定当前明确归属，不新增跨期分摊。 |

#### ADC-CYC-074 切 TAB 不改账单和钱包

| 项目 | 设计 |
| --- | --- |
| 字段 | 真实净值数据TAB、分润清算TAB |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | X有B1、两笔有效收款、已显示清算TAB；记录钱包108、B1应收1.75/实收1.75和起点T1。 |
| 步骤 | 1. 保存账单、receipt、资金流与当前基准的数量/金额。<br>2. 真实点击切换两个TAB各三次并刷新。<br>3. 重新读取DB/API并逐项比较，不因GET产生写动作。 |
| 明确预期 | 反复切换两TAB后账单/receipt数量、金额、钱包108和起点T1均不变；每次请求对象为X、范围与所选TAB一致。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N01/N02 账户清算范围指标/曲线；N21/N22 账单和到账；N23 正式登记。范围参数以接口文档为准。 |
| 检查点 | V2-REC-027、V2-UI-009 |
| 规则依据 | T J0/J1、Q8、7.1—7.4；P 首次到账显示 TAB 与最新确认账单触发起点。 |

#### ADC-CYC-075 清算不删除账户全历史

| 项目 | 设计 |
| --- | --- |
| 字段 | 清算统计起点、真实净值数据TAB |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | X全历史收益50、累计费用5；触发新周期后周期利润3；旧/新策略运行证据均存在。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 真实净值数据TAB仍保留全历史收益50、费用5及全部运行段；清算TAB只按其本期输入显示3，不能用新周期3覆盖全历史50。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N01/N02 账户清算范围指标/曲线；N21/N22 账单和到账；N23 正式登记。范围参数以接口文档为准。 |
| 检查点 | V2-REC-028、V2-SEG-002 |
| 规则依据 | T J0/J1、Q8、7.1—7.4；P 首次到账显示 TAB 与最新确认账单触发起点。 |

#### ADC-CYC-076 刷新与重新登录恢复同一基准

| 项目 | 设计 |
| --- | --- |
| 字段 | 分润清算TAB、清算统计起点 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B2 T2已有首次confirmed，B1晚补录已完成；页面基准T2。 |
| 步骤 | 1. 准备本条独立输入，记录源身份及新发布代次。<br>2. 打开账户 X 详情，读取对应接口及目标字段；核对原值、页面值、对象与时间范围。 |
| 明确预期 | 浏览器刷新、返回详情、同身份重新登录后仍显示TAB且起点T2；不恢复会话内旧T1、不重放登记。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N01/N02 账户清算范围指标/曲线；N21/N22 账单和到账；N23 正式登记。范围参数以接口文档为准。 |
| 检查点 | V2-REC-021、V2-UI-009 |
| 规则依据 | T J0/J1、Q8、7.1—7.4；P 首次到账显示 TAB 与最新确认账单触发起点。 |

## 到账登记与收款详情（文档条件设计，现场空表未打开弹窗）

#### ADC-CYC-077 保留原表单并直接确认成功

| 项目 | 设计 |
| --- | --- |
| 字段 | 到账登记弹窗、到账金额、到账时间 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | 指定商务甲登录；B1应收1.75、未收1.75；到账金额0.75，实际到账10-03 16:00；凭证不选。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 原表单金额和时间准确；成功后产生一笔0.75的confirmed记录、待收1.00，不新增“提交后再审核/独立清算”环节，不要求填写产品未保留的额外可见ID。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-UI-014、V2-UI-015、V2-REC-010 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-078 金额等于剩余应收可收足

| 项目 | 设计 |
| --- | --- |
| 字段 | 到账金额 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1已confirmed0.75、最新version；本次到账1.00。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 新增一笔1.00 confirmed；累计1.75、待收0；单据关联B1，B2不变。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-011 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-079 金额为零拒绝

| 项目 | 设计 |
| --- | --- |
| 字段 | 到账金额 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1待收1.75；到账金额0，其他字段合法。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 表单/接口拒绝0；无新receipt，实收缺失状态和未收1.75保持，不以0元确认显TAB。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-013、V2-REC-018 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-080 负金额拒绝

| 项目 | 设计 |
| --- | --- |
| 字段 | 到账金额 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1待收1.75；到账金额−0.01，其他字段合法。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 拒绝负金额；不形成退款/冲销，不减少实收、不增加待收、不改变起点。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-013 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-081 金额为空拒绝

| 项目 | 设计 |
| --- | --- |
| 字段 | 到账金额 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1待收1.75；金额字段留空/请求中不传，各作为独立参数化实例。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 两实例均拒绝；不自动默认剩余1.75或0，未发生任何confirmed写入。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-013 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-082 单笔超过应收拒绝

| 项目 | 设计 |
| --- | --- |
| 字段 | 到账金额 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1未收1.75；提交1.76。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 拒绝整笔1.76，无截取1.75的部分记账；B1原应收1.75/原实收状态不变。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-012 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-083 累计超过剩余拒绝

| 项目 | 设计 |
| --- | --- |
| 字段 | 到账金额 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1已收0.75、剩1.00；本次1.01。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 拒绝整笔1.01，原confirmed0.75保留、剩余仍1.00；不把累计1.76四舍五入成合法。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-012 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-084 超过支持小数精度拒绝或按已批准契约处理

| 项目 | 设计 |
| --- | --- |
| 字段 | 到账金额 |
| 优先级 / 状态 | P1 / 待口径绑定 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | 读取真实金额精度p；若当前p=2则提交0.751；若p不同，用恰p+1位且非零尾数的金额，仍小于应收。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 按当前已确认金额契约拒绝超精度或明确标准化；不得静默用显示两位替代真实入账值。必须在执行前将p及唯一结果写入此例。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-013、V2-UI-011 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |
| 待核实/补充 | 原始文档摘要未提供字段最大precision，不擅设为2位；这条未绑定前不计可执行完成。 |

#### ADC-CYC-085 时区等价输入同一绝对时刻

| 项目 | 设计 |
| --- | --- |
| 字段 | 到账时间 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | 独立两张同金额账单，分别提交2026-10-03T08:00:00Z与2026-10-03T16:00:00+08:00。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 两笔保存同一绝对时刻，页面均显示北京时间10-03 16:00；不对带+08:00值重复加8小时。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-015、V2-UI-013 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-086 跨月晚补录保留实际到账时间

| 项目 | 设计 |
| --- | --- |
| 字段 | 到账时间 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1于09-30 23:50触发；10-02操作登记实际到账09-30 23:59:30，金额0.75。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 实际到账归属09-30 23:59:30；触发时间与操作时间分开，页面起点仍09-30 23:50；若月统计按到账时刻则归9月，不按操作时刻归10月。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-015、V2-REC-019 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-087 凭证为空仍可登记

| 项目 | 设计 |
| --- | --- |
| 字段 | 到账凭证 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1剩1.75，指定商务提交0.75、合法到账时间，无附件。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 成功confirmed0.75、待收1.00；不因缺凭证阻断，不额外加审核。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-014 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-088 带凭证准确关联本次收款

| 项目 | 设计 |
| --- | --- |
| 字段 | 到账凭证 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1登记0.75并上传合法测试凭证F1；另账单B2已有不同F2。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | R1关联F1，查看R1可读取F1；不串到F2；确认金额仍0.75，上传/查看不产生第二笔receipt。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-014、V2-UI-021 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-089 指定当前商务允许确认

| 项目 | 设计 |
| --- | --- |
| 字段 | 操作、到账登记弹窗 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | X当前商务甲，甲拥有X资源访问；B1未收1.75，最新版本。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 甲看见登记入口且N23可成功确认0.75，审计actor=甲、bill=B1；没有额外审核状态。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-016、V2-UI-019 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-090 其他商务禁止确认

| 项目 | 设计 |
| --- | --- |
| 字段 | 操作、到账登记弹窗 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | 乙为有效商务但X当前指定商务为甲；乙持有效会话并可构造B1请求。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 登记入口隐藏/不可用；直接N23拒绝，B1金额、receipt和周期指针均不变。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-017、V2-UI-020 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-091 非指定商务的管理员也禁止

| 项目 | 设计 |
| --- | --- |
| 字段 | 操作、到账登记弹窗 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | 丙拥有管理员角色，但X指定商务是甲，丙不是甲。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 页面不提供确认权；直接N23拒绝，不能由管理员角色绕过当前商务校验；没有部分记账。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-017、V2-UI-020 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-092 无会话不能登记

| 项目 | 设计 |
| --- | --- |
| 字段 | 操作 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1合法待收；N23请求不带认证，金额0.75。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 按鉴权契约拒绝未认证请求；无新增receipt、不显首次清算TAB、不改变账单version。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-UI-021 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-093 失效会话不能登记

| 项目 | 设计 |
| --- | --- |
| 字段 | 操作 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | 弹窗打开后登录会话过期，再提交0.75。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 提示重新认证/鉴权失败；业务未落款。重新登录后先回查账单状态，不把失效提交误显示成功。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-UI-021 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-094 替换账单账户 ID 防串户

| 项目 | 设计 |
| --- | --- |
| 字段 | 账号ID、操作 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | 甲为X商务，仅X有权限；Y账单BY不属于甲；把提交中B1替换BY，或account=X但bill=BY。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 两种请求分别拒绝；X、Y的应收/实收均不变；不能以“甲登录”跳过资源与账单归属检查。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-UI-021、V2-REC-017 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-095 替换他人凭证 ID 拒绝读取或绑定

| 项目 | 设计 |
| --- | --- |
| 字段 | 到账凭证 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | 甲能操作X/B1；F2属于不可访问Y/BY；在上传关联/下载请求替换为F2。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 绑定与读取按真实资源权限分别拒绝；R1不会关联F2，不泄露文件内容/元数据；原B1金额无变化。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-UI-021 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-096 同幂等键同内容仅一笔

| 项目 | 设计 |
| --- | --- |
| 字段 | 到账金额 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1待收1.75、bill_version=v；金额0.75、时间固定；Idempotency-Key=K1，同body连续发两次。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 最终一笔有效confirmed0.75，累计0.75/待收1.00；重放响应按契约返回原业务结果，不能因旧version再计一笔或误报新业务成功。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-023 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-097 同幂等键不同内容冲突

| 项目 | 设计 |
| --- | --- |
| 字段 | 到账金额 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | K1第一次成功0.75；仍用K1把body金额改为0.50，其他身份不变。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 第二次冲突拒绝；累计仍0.75/剩1.00；不修改第一笔，不创建0.50的新receipt。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-023 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-098 双击登记不产生两笔

| 项目 | 设计 |
| --- | --- |
| 字段 | 到账金额 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | 弹窗填0.75、真实连续双击提交；记录浏览器实际发送请求及键。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 前端应按当前请求策略复用同一业务幂等键或防重复提交；最终仅一笔0.75，不能因两个HTTP成功显示收1.50。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-023 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-099 旧账单 version 冲突不覆盖他人收款

| 项目 | 设计 |
| --- | --- |
| 字段 | 到账金额 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | 客户端A读取B1 version=v；客户端B先成功收0.75，version变新；A用旧v及新幂等键提交0.50。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | A按版本契约冲突，不能覆盖B；累计仍0.75、待收1.00。A刷新最新对象后可另发一次合法新操作。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-024、V2-REC-023 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-100 并发超收至多一笔有效

| 项目 | 设计 |
| --- | --- |
| 字段 | 到账金额 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1已收0.75、剩1.00；两个客户端同时读最新v，用不同K分别提交0.75。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 不允许最终累计2.25；至多一个0.75成功，最终若一笔成功则累计1.50、剩0.25；另请求因版本/超收拒绝且无残留。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-024 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-101 合法并发更新不能丢成功记录

| 项目 | 设计 |
| --- | --- |
| 字段 | 到账金额 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1剩1.75；两个请求金额0.50和0.75，不同K；若其中一个因版本拒绝，取最新v按真实终态重试该未成功请求。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 最终两次真实成功分别保留，累计1.25、剩0.50；允许版本冲突再恢复，不要求首次并发都成功；不能丢任一已成功收款。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-024 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-102 响应丢失但服务端已成功恢复

| 项目 | 设计 |
| --- | --- |
| 字段 | 到账金额 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | N23以K1提交0.75，服务端已提交confirmed但响应被受控丢弃。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 先查原单/receipt判定已成功；复用K1同body重试后仍仅0.75一笔，剩1.00；页面从未知变明确成功，不能另发新K重复收。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-025 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-103 请求未提交的超时恢复

| 项目 | 设计 |
| --- | --- |
| 字段 | 到账金额 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | N23的K1=0.75在服务端业务提交前失败；确认B1无该receipt且金额仍未收1.75。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 同K1同body重试最终成功一次，累计0.75/剩1.00；在恢复前不把超时当已收。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-026 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-104 无幂等键不宣称跨重试安全

| 项目 | 设计 |
| --- | --- |
| 字段 | 到账金额 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | 独立合法登记未带Idempotency-Key；其他字段符合真实请求契约。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 首次响应按文档处理；不能仅因两次body相同就断言系统保证跨重试去重。此例检查调用方使用策略及实际契约，不发起第二次可能重复收款来强造通过。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-023 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |
| 待核实/补充 | 接口文档明确普通写入建议幂等键，缺键无跨重试去重保证；自动化恢复路径必须发送稳定键。 |

#### ADC-CYC-105 查看详情没有副作用

| 项目 | 设计 |
| --- | --- |
| 字段 | 收款详情 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1应收1.75、已confirmed R1=0.75；当前钱包108、后继周期利润3。 |
| 步骤 | 1. 保存原单、receipt、资金流、wallet、period、tab_start。<br>2. 打开关闭收款详情三次，刷新后再开一次。<br>3. 对照所有保存项及实际GET请求，确认没有写动作。 |
| 明确预期 | 多次开关详情只读取B1和R1；应收1.75、实收0.75、钱包108、利润3、账单/receipt数量与TAB起点均不变。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-027 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-106 详情逐笔对账

| 项目 | 设计 |
| --- | --- |
| 字段 | 收款详情、到账金额、到账时间、到账凭证 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1应收1.75；R1=0.75/10-03 16:00/凭证F1，R2=1.00/10-04 11:00/无凭证。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 详情两笔各一次，金额合计1.75；时间、凭证状态分别对应R1/R2，不把F1复制给R2；未收为0。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-010、V2-REC-011、V2-REC-014、V2-REC-015 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-107 取消弹窗不产生登记

| 项目 | 设计 |
| --- | --- |
| 字段 | 到账登记弹窗 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1剩1.75；填0.75和合法时间，未点最终提交，点击取消/关闭。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 无N23成功写入、无新receipt，实收/未收/基准不变；再次打开表单按现有交互重置或保留，但不能显示已登记成功。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-UI-014、V2-REC-027 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-108 登记成功后列表回读而非仅本地加数

| 项目 | 设计 |
| --- | --- |
| 字段 | 到账登记弹窗 |
| 优先级 / 状态 | P1 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1提交0.75真实成功；表格刷新可能遇到旧响应晚到。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 最终列表/详情应收1.75、confirmed实收0.75、剩1.00；不因旧响应恢复未收状态或重复本地累加成1.50；首次confirmed TAB状态能持久恢复。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-UI-009、V2-REC-010、V2-REC-019 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

#### ADC-CYC-109 旧 reported 记录不当作新版已确认

| 项目 | 设计 |
| --- | --- |
| 字段 | 操作、收款详情 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | 另准备兼容历史receipt R0 status=reported、amount0.75；没有confirmed；B1应收1.75。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | R0不计confirmed实收，不据此显示首次清算TAB；新版N23仍是直接confirmed主链，不要求先调用N28审核或N24清算。兼容入口另测。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-AGG-014、V2-REC-018 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |
| 待核实/补充 | N24/N28仅用于兼容专项；本模块不恢复旧reported→审核→独立清算流程。 |

#### ADC-CYC-110 逾期部分未收人工跟进

| 项目 | 设计 |
| --- | --- |
| 字段 | 触发分润时间、实收分润金额、操作 |
| 优先级 / 状态 | P1 / 待口径绑定 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1触发至当前24小时以上，已收0.75、剩1.00；策略来源仍running。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 保留原应收1.75、实收0.75、剩1.00并按当前逾期口径呈现/人工跟进；不得自动停止策略、自动出金、重复制单或新增审核。24小时起算字段和边界需绑定技术7.6。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-BILL-029、V2-REC-010 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |
| 待核实/补充 | 固定禁止额外资金/控制副作用；逾期从trigger还是通知时刻起算需按7.6当前正文精确绑定，不能用猜测形成缺陷。 |

#### ADC-CYC-111 待收旧单与策略切换隔离

| 项目 | 设计 |
| --- | --- |
| 字段 | 分润比例、应收分润金额 |
| 优先级 / 状态 | P0 / 设计完成/待执行 |
| 前置 | 用专用账户 X（NT code=qa_ad_cyc_x）准备数据；2999 仅作页面观察样本，不直接覆盖其数据。以下每条独立恢复前置；所有时间为 UTC+8。DB 只准备输入和前置状态，目标业务动作经真实任务或 API 产生。 |
| 具体输入 | B1在A策略期间触发1.75、未收；X后切B；商务甲此时给B1登记0.75。 |
| 步骤 | 1. 准备原账单、当前商务和最新版本，打开对应账单登记弹窗。<br>2. 输入本条实际到账金额/时间/凭证状态，提交一次真实 N23；保留请求身份和结果。<br>3. 回读原单与receipt、检查钱包/周期基准；核对弹窗和历史分润状态。 |
| 明确预期 | 原B1仍对应原触发快照/35%费率，收款0.75只补B1；不因切策略生成新应收或改写旧策略归属；X全历史连续保留。 |
| DB入口 | quant_account_profiles；quant_profit_share_policies；quant_profit_share_cycles；quant_profit_share_bills；quant_profit_share_bill_attributions；quant_profit_share_receipts；fact_performance_sample；fact_nav_sample；exchange_account_cash_flows；account_strategy_assignment；account_strategy_migration |
| 真实API/处理 | N23 正式登记到账 → 直接 confirmed；N21/N22 回读原账单/到账记录。普通写入 JSON Body，使用最新 bill_version 和本条规定的 Idempotency-Key。 |
| 检查点 | V2-REC-028、V2-SEG-002、V2-BILL-018 |
| 规则依据 | T M1/M2、7.3；接口文档调用约定及 N23；N24/N28 仅兼容。 |

## 本稿计数与剩余绑定

共 111 条稳定用例（ADC-CYC-001 至 ADC-CYC-111），其中 10 条待口径绑定；全部本轮未执行。

| 模块 | 用例数 |
| --- | --- |
| 已分配策略（现场可见 13 列） | 34 |
| 历史分润（现场可见 12 列；2999 当前为空） | 20 |
| 分润清算 TAB 与周期基准（条件设计，现场未出现） | 22 |
| 到账登记与收款详情（文档条件设计，现场空表未打开弹窗） | 35 |

待口径绑定项保留在对应条目：策略ID具体显示字段、A→B→A行组织、历史分润前后已实现/钱包快照语义、多次到账主表时间、历史商务显示语义、到账金额最大精度、跨周期轮次归属、24小时起算边界。所有项目都不修改已确认收益率/应收/周期公式。
