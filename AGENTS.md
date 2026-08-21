# QuestTest 开发约束

`docs/automation-test-framework-design.md` 是本项目的架构基线。新增、修改和重构代码前必须先遵守其目录职责、依赖方向和敏感信息规则。

- 依赖方向固定为 `tests -> service -> api/db`；`tests` 可直接调用 `api`、`db` 和 `tools`，但 `api`、`db` 不得反向依赖 `service` 或 `tests`。
- 测试场景只放在 `tests/cases/`，不得在 Case 中拼接复杂 URL、散落 SQL 或复制完整业务流程。
- API 层只封装协议和端点语义；Service 层只编排业务动作；DB 层只处理连接、事务和实体数据访问；`tools/` 只放无业务含义工具。
- 测试环境允许 DB 写入、事务和数据清理；生产环境不得配置写入凭据，也不得执行 DB 写操作。
- 用户明确提供的测试环境地址、账号和凭据可以写入 `config/test.yaml` 供本地测试使用；不得写入生产凭据，且不得把包含真实凭据的文件提交到版本库。日志仍不得输出密码或完整 Token。
- 不额外引入 `core`、`domain`、`ports`、`adapters` 等分层，除非已确认存在跨项目复用、多实现或依赖倒置的真实需求。
- 所有公共方法都应具有类型标注和说明输入、输出、异常行为的 docstring。
- 需求与本文件或架构设计文档冲突时，停止实现并先向用户确认。
