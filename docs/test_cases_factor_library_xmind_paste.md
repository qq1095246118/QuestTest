# 因子库接口用例
## Auth
### 注册
#### 使用新邮箱注册成功
##### 使用未注册邮箱、长度不少于 8 位的密码、display_name 请求 POST /api/v1/auth/register
###### 返回 HTTP 200，success=true，data.user.email 等于注册邮箱，响应和数据库中均不出现明文密码
#### 已存在邮箱注册失败
##### 使用已经存在的邮箱请求 POST /api/v1/auth/register
###### 返回明确的邮箱已存在错误，不返回 token，不新增重复用户
#### 密码长度不足注册失败
##### 使用未注册邮箱和长度小于 8 位的密码请求 POST /api/v1/auth/register
###### 返回参数错误，用户不被创建，不返回 500
#### 缺少邮箱注册失败
##### 不传 email 或 email 为空请求 POST /api/v1/auth/register
###### 返回参数错误，用户不被创建，不返回 500
#### 非法邮箱格式注册失败
##### 使用非法邮箱格式请求 POST /api/v1/auth/register
###### 返回参数错误，用户不被创建，不返回 500
### 登录
#### 有效账号登录成功
##### 使用有效邮箱和密码请求 POST /api/v1/auth/login
###### 返回 HTTP 200，success=true，data.token 不为空，data.user.email 等于登录邮箱
#### 错误密码登录失败
##### 使用有效邮箱和错误密码请求 POST /api/v1/auth/login
###### 返回认证失败错误，不返回 token，不返回用户敏感信息
#### 缺少邮箱登录失败
##### 不传 email 或 email 为空请求 POST /api/v1/auth/login
###### 返回参数错误或认证失败，不返回 token，不返回 500
#### 缺少密码登录失败
##### 不传 password 或 password 为空请求 POST /api/v1/auth/login
###### 返回参数错误或认证失败，不返回 token，不返回 500
#### 非法邮箱格式登录失败
##### 使用非法邮箱格式和任意密码请求 POST /api/v1/auth/login
###### 返回参数错误或认证失败，不返回 token，不返回 500
#### 连续 5 次错误密码未锁定
##### 使用同一有效邮箱连续 5 次输入错误密码请求 POST /api/v1/auth/login
###### 前 5 次均返回登录失败，该账号仍未被锁定
#### 第 6 次错误密码触发锁定
##### 在同一账号连续 5 次失败后再次使用错误密码请求 POST /api/v1/auth/login
###### 返回账号锁定错误，账号进入锁定状态
#### 锁定后正确密码登录失败
##### 对已锁定账号使用正确密码请求 POST /api/v1/auth/login
###### 返回账号锁定错误，不返回 token
#### 管理员解锁后登录成功
##### 管理员调用解锁接口后，再使用正确密码请求 POST /api/v1/auth/login
###### 返回 HTTP 200，success=true，账号可正常登录，失败次数被重置
### 当前用户
#### 有效 token 查询当前用户成功
##### 使用登录返回的 token 请求 GET /api/v1/me
###### 返回 HTTP 200，success=true，data.email 等于登录邮箱
#### 未带 token 查询当前用户失败
##### 不带 Authorization 请求 GET /api/v1/me
###### 返回 401 或 403，不返回当前用户资料
#### 无效 token 查询当前用户失败
##### 使用伪造 token 请求 GET /api/v1/me
###### 返回 401 或 403，不返回当前用户资料
#### token 对应用户不存在查询失败
##### 使用已删除或不存在用户对应的 token 请求 GET /api/v1/me
###### 返回用户不存在错误，不返回 500
### 连贯场景
#### 注册后登录并查询当前用户成功
##### 先注册新用户，再使用该用户登录，最后使用登录 token 请求 GET /api/v1/me
###### 注册、登录、当前用户三个接口均成功，三个接口中的邮箱一致
#### 登录失败锁定后由管理员解锁成功
##### 对同一账号连续输错密码触发锁定，再由管理员解锁，最后使用正确密码登录
###### 锁定前后状态变化符合规则，解锁后登录成功
## Approval
### 审批列表
#### 查询审批列表成功
##### 使用有审批权限的 token 请求 GET /api/v1/approvals
###### 返回 HTTP 200，success=true，data 为审批分页列表，列表项包含审批对象、类型、状态和提交人信息
#### 按 pending 状态筛选审批列表
##### 使用 status=pending 请求 GET /api/v1/approvals
###### 返回的审批项状态均为 pending，分页总数与数据库只读查询一致
#### 按 approved 状态筛选审批列表
##### 使用 status=approved 请求 GET /api/v1/approvals
###### 返回的审批项状态均为 approved，分页总数与数据库只读查询一致
#### 按 rejected 状态筛选审批列表
##### 使用 status=rejected 请求 GET /api/v1/approvals
###### 返回的审批项状态均为 rejected，分页总数与数据库只读查询一致
#### 按 cancelled 状态筛选审批列表
##### 使用 status=cancelled 请求 GET /api/v1/approvals
###### 返回的审批项状态均为 cancelled，分页总数与数据库只读查询一致
#### 按 target_type 筛选母因子审批
##### 使用 target_type=factor 请求 GET /api/v1/approvals
###### 返回的审批对象均为母因子相关审批
#### 按 target_type 筛选子因子审批
##### 使用 target_type=sub_factor 请求 GET /api/v1/approvals
###### 返回的审批对象均为子因子相关审批
#### 按 target_type 筛选主题审批
##### 使用 target_type=theme 请求 GET /api/v1/approvals
###### 返回的审批对象均为主题相关审批
#### 按 entity_type 筛选审批列表
##### 使用接口文档支持的 entity_type 请求 GET /api/v1/approvals
###### 返回结果与 entity_type 对应的业务对象一致，entity_type 与 target_type 均可作为筛选条件使用
#### 按 request_type 筛选审批列表
##### 使用接口文档支持的 request_type 请求 GET /api/v1/approvals
###### 返回的审批项请求类型均符合筛选条件
#### 审批列表分页查询成功
##### 使用 page 和 limit 请求 GET /api/v1/approvals
###### 返回分页结构正确，items 数量不超过 limit，total 与数据库只读查询一致
#### 未带 token 查询审批列表失败
##### 不带 Authorization 请求 GET /api/v1/approvals
###### 返回 401 或 403，不返回审批列表数据
### 创建审批
#### 提交母因子更新审批成功
##### 对没有 pending 审批的母因子请求对应 with-approval 更新接口
###### 返回审批提交成功，生成 pending 审批，正式母因子数据暂不变更
#### 提交母因子状态审批成功
##### 对没有 pending 审批的母因子请求对应 with-approval 状态接口
###### 返回审批提交成功，生成 pending 审批，正式母因子状态暂不变更
#### 提交子因子更新审批成功
##### 对没有 pending 审批的子因子请求对应 with-approval 更新接口
###### 返回审批提交成功，生成 pending 审批，正式子因子数据暂不变更
#### 提交子因子状态审批成功
##### 对没有 pending 审批的子因子请求对应 with-approval 状态接口
###### 返回审批提交成功，生成 pending 审批，正式子因子状态暂不变更
#### 提交主题更新审批成功
##### 对没有 pending 审批的主题请求对应 with-approval 更新接口
###### 返回审批提交成功，生成 pending 审批，正式主题数据暂不变更
#### 提交主题状态审批成功
##### 对没有 pending 审批的主题请求对应 with-approval 状态接口
###### 返回审批提交成功，生成 pending 审批，正式主题状态暂不变更
#### 同一对象 pending 审批存在时重复提交失败
##### 对同一业务对象先提交一个 pending 审批，再次提交任意类型审批
###### 第二次提交返回重复审批或对象正在审批错误，不生成新的 pending 审批
#### 更新审批存在时提交删除或状态审批失败
##### 对同一子因子先提交更新审批，再提交删除或状态变更审批
###### 第二次提交失败，说明同一对象任意 pending 审批都会占用该对象
#### 历史审批终态后允许重新提交
##### 对同一对象先提交审批并处理为 approved、rejected 或 cancelled，再重新提交审批
###### 新审批可以正常提交，历史终态审批不再占用该对象
#### 无实际变更提交更新审批失败
##### 使用与当前业务对象完全一致的数据请求 with-approval 更新接口
###### 返回无变更或参数错误，不生成 pending 审批
#### 无审批权限提交审批失败
##### 使用没有审批提交权限的账号请求 with-approval 接口
###### 返回 401 或 403，不生成审批记录
### 审批详情
#### 查询 pending 审批详情成功
##### 使用真实 pending approval_id 请求 GET /api/v1/approvals/{id}
###### 返回审批详情成功，详情中包含审批对象、请求内容和创建日志
#### 查询 approved 审批详情成功
##### 使用真实 approved approval_id 请求 GET /api/v1/approvals/{id}
###### 返回审批详情成功，详情中包含通过日志
#### 查询 rejected 审批详情成功
##### 使用真实 rejected approval_id 请求 GET /api/v1/approvals/{id}
###### 返回审批详情成功，详情中包含拒绝日志
#### 查询 cancelled 审批详情成功
##### 使用真实 cancelled approval_id 请求 GET /api/v1/approvals/{id}
###### 返回审批详情成功，详情中包含取消日志
#### 查询不存在审批详情失败
##### 使用不存在的 approval_id 请求 GET /api/v1/approvals/{id}
###### 返回 404 或明确错误，不返回 500
### 处理审批
#### 审批通过后业务数据生效
##### 对 pending 审批请求 PATCH /api/v1/approvals/{id} 并传 approve 动作
###### 审批状态变为 approved，对应业务对象数据按审批内容生效，审批日志新增通过记录
#### 审批拒绝后业务数据不生效
##### 对 pending 审批请求 PATCH /api/v1/approvals/{id} 并传 reject 动作
###### 审批状态变为 rejected，对应业务对象保持原数据，审批日志新增拒绝记录
#### 已通过审批不能再次处理
##### 对 approved 审批再次请求 PATCH /api/v1/approvals/{id}
###### 返回审批已终态错误，业务数据不重复变更
#### 已拒绝审批不能再次处理
##### 对 rejected 审批再次请求 PATCH /api/v1/approvals/{id}
###### 返回审批已终态错误，业务数据不变
#### 已取消审批不能再次处理
##### 对 cancelled 审批请求 PATCH /api/v1/approvals/{id}
###### 返回审批已终态错误，业务数据不变
#### 有权限用户可以处理任意人的审批
##### 使用另一个具备审批权限的账号处理他人提交的 pending 审批
###### 审批处理成功，审批不绑定到提交人本人
### 取消审批
#### 取消 pending 审批成功
##### 对 pending 审批请求 DELETE /api/v1/approvals/{id}
###### 审批状态变为 cancelled，对应业务对象不变，审批日志新增取消记录
#### 取消后释放对象审批占用
##### 先取消某对象的 pending 审批，再对同一对象重新提交审批
###### 新审批提交成功，说明 cancelled 不再占用业务对象
#### 已通过审批不能取消
##### 对 approved 审批请求 DELETE /api/v1/approvals/{id}
###### 返回审批已终态错误，业务数据不变
#### 已拒绝审批不能取消
##### 对 rejected 审批请求 DELETE /api/v1/approvals/{id}
###### 返回审批已终态错误，业务数据不变
#### 有权限用户可以取消任意人的审批
##### 使用另一个具备审批权限的账号取消他人提交的 pending 审批
###### 取消成功，审批不绑定到提交人本人
### 批量审批
#### 批量通过全部成功
##### 使用多个 pending approval_id 请求 POST /api/v1/approvals/batch/approve
###### 所有审批状态变为 approved，所有对应业务对象数据生效
#### 批量通过第二个失败时中断
##### 使用第一个可通过、第二个不可通过、后续仍 pending 的 approval_id 列表请求 POST /api/v1/approvals/batch/approve
###### 第一个审批成功且不回滚，第二个审批失败，第二个之后的审批不再执行
#### 批量通过包含不存在审批失败
##### 在 approval_id 列表中加入不存在的 id 请求 POST /api/v1/approvals/batch/approve
###### 接口返回失败信息，已执行成功的审批不回滚，失败项之后不继续处理
#### 批量通过空列表失败
##### 使用空 approval_id 列表请求 POST /api/v1/approvals/batch/approve
###### 返回参数错误，不返回 500
## factor
### 母因子列表
#### 默认分页查询母因子列表成功
##### 使用有效 token 请求 GET /api/v1/factors
###### 返回 HTTP 200，success=true，分页结构完整，默认 limit 为 50 或符合接口文档默认规则
#### 指定 page 和 limit 查询母因子列表
##### 使用 page=1、limit=5 请求 GET /api/v1/factors
###### 返回 items 数量不超过 5，分页 total 与数据库只读查询一致
#### limit 最大值 500 生效
##### 使用 limit=500 请求 GET /api/v1/factors
###### 返回 items 数量不超过 500，接口不返回 500
#### limit 超过 500 返回明确结果
##### 使用 limit=501 请求 GET /api/v1/factors
###### 返回参数错误或按最大 500 限制处理，不返回 500
#### 按 status=1 查询新挖库母因子
##### 使用 status=1 请求 GET /api/v1/factors
###### 返回母因子的 factors_details.status 均为 1，接口数据与数据库一致
#### 按 status=2 查询有效库母因子
##### 使用 status=2 请求 GET /api/v1/factors
###### 返回母因子的 factors_details.status 均为 2，接口数据与数据库一致
#### 按 status=3 查询失效库母因子
##### 使用 status=3 请求 GET /api/v1/factors
###### 返回母因子的 factors_details.status 均为 3，接口数据与数据库一致
#### status=4 不展示
##### 使用 status=4 请求 GET /api/v1/factors
###### 返回空结果或明确不支持结果，列表中不展示删除状态母因子
#### status 与 factor_detail_status 同值查询一致
##### 分别使用 status=2 和 factor_detail_status=2 请求 GET /api/v1/factors
###### 两种筛选结果中的母因子详情状态一致，分页 total 与数据库一致
#### 按主题筛选母因子列表
##### 使用 factor_theme 请求 GET /api/v1/factors
###### 返回母因子均关联该主题，接口数据与数据库主题关联一致
#### 按 time_window 筛选母因子列表
##### 使用真实 time_window 请求 GET /api/v1/factors
###### 返回母因子的窗口指标与筛选窗口一致，接口数据与数据库一致
#### 按 created_by 筛选母因子列表
##### 使用真实 created_by 请求 GET /api/v1/factors
###### 返回母因子的创建人均符合筛选条件，分页 total 与数据库一致
#### 按创建时间范围筛选母因子列表
##### 使用 created_from 和 created_to 请求 GET /api/v1/factors
###### 返回母因子的创建时间均在范围内，分页 total 与数据库一致
#### 按 operator_by 筛选母因子列表
##### 使用真实 operator_by 请求 GET /api/v1/factors
###### 返回母因子的操作人均符合筛选条件，分页 total 与数据库一致
#### 按操作时间范围筛选母因子列表
##### 使用 operated_from 和 operated_to 请求 GET /api/v1/factors
###### 返回母因子的操作时间均在范围内，分页 total 与数据库一致
#### 按 updated_at 升序查询母因子列表
##### 使用 sort_by=updated_at、sort_order=asc 请求 GET /api/v1/factors
###### 返回数据按 updated_at 升序排列，与数据库排序一致
#### 按 updated_at 降序查询母因子列表
##### 使用 sort_by=updated_at、sort_order=desc 请求 GET /api/v1/factors
###### 返回数据按 updated_at 降序排列，与数据库排序一致
#### 使用排序别名查询母因子列表
##### 使用 order_by、sort_field、order、direction 中接口文档支持的别名请求 GET /api/v1/factors
###### 排序结果与 sort_by、sort_order 对应查询一致
#### 未带 token 查询母因子列表失败
##### 不带 Authorization 请求 GET /api/v1/factors
###### 返回 401 或 403，不返回母因子列表
### 母因子创建
#### 创建母因子成功
##### 使用 serial_prefix、factor_name、cn_name、theme_id 请求 POST /api/v1/factors
###### 返回创建成功，新母因子 status=1，新挖库数据与数据库一致
#### factor_theme 不存在时自动创建主题
##### 使用不存在的 factor_theme 请求 POST /api/v1/factors
###### 母因子创建成功，同时自动创建对应主题并建立关联
#### cn_name 不传时回退 factor_name
##### 只传 factor_name 不传 cn_name 请求 POST /api/v1/factors
###### 创建成功，返回和数据库中的 cn_name 等于 factor_name
#### formula_summary 写入解释字段
##### 创建母因子时传 formula_summary 请求 POST /api/v1/factors
###### 创建成功，factors_details.explanation 与 formula_summary 一致
#### 缺少 serial_prefix 创建失败
##### 不传 serial_prefix 请求 POST /api/v1/factors
###### 返回参数错误，不创建母因子，不返回 500
#### 缺少 factor_name 创建失败
##### 不传 factor_name 请求 POST /api/v1/factors
###### 返回参数错误，不创建母因子，不返回 500
#### 缺少主题创建失败
##### 不传 theme_id、theme_ids、factor_theme 请求 POST /api/v1/factors
###### 返回参数错误，不创建母因子，不返回 500
#### serial_prefix 非字母开头创建失败
##### 使用数字或特殊字符开头的 serial_prefix 请求 POST /api/v1/factors
###### 返回参数错误，不创建母因子
#### serial_prefix 包含非法字符创建失败
##### 使用包含字母数字下划线以外字符的 serial_prefix 请求 POST /api/v1/factors
###### 返回参数错误，不创建母因子
#### factor_name 重复创建失败
##### 使用已存在 factor_name 请求 POST /api/v1/factors
###### 返回重复错误，不创建重复母因子
#### cn_name 重复创建失败
##### 使用已存在 cn_name 请求 POST /api/v1/factors
###### 返回重复错误，不创建重复母因子
#### 请求中传 status 不改变默认新挖状态
##### 创建母因子时额外传 status=2 或 status=3 请求 POST /api/v1/factors
###### 创建成功时仍为 status=1，或返回参数错误，不允许创建到有效库或失效库
### 母因子详情
#### 查询母因子详情成功
##### 使用真实 factor_id 请求 GET /api/v1/factors/{factorId}
###### 返回母因子详情成功，factor_detail、themes、metrics、activities 与数据库一致
#### 查询不存在母因子失败
##### 使用不存在的 factor_id 请求 GET /api/v1/factors/{factorId}
###### 返回 404 或明确错误，不返回 500
#### 未带 token 查询母因子详情失败
##### 不带 Authorization 请求 GET /api/v1/factors/{factorId}
###### 返回 401 或 403，不返回母因子详情
### 母因子直接更新
#### 直接更新母因子成功
##### 对没有 pending 审批的母因子请求 PUT /api/v1/factors/{factorId}
###### 返回更新成功，母因子数据立即生效并与数据库一致
#### 更新 factor_name 重复失败
##### 将母因子 factor_name 修改为已存在名称请求 PUT /api/v1/factors/{factorId}
###### 返回重复错误，原母因子数据不变
#### 更新 cn_name 重复失败
##### 将母因子 cn_name 修改为已存在中文名请求 PUT /api/v1/factors/{factorId}
###### 返回重复错误，原母因子数据不变
#### 无变更直接更新失败
##### 使用与当前母因子完全一致的数据请求 PUT /api/v1/factors/{factorId}
###### 返回无变更或参数错误，不产生更新时间变化
#### pending 审批存在时直接更新失败
##### 对已有 pending 审批的母因子请求 PUT /api/v1/factors/{factorId}
###### 返回对象正在审批错误，不能绕过审批直接更新
### 母因子状态
#### 新挖库母因子更新为有效库成功
##### 对 status=1 母因子请求 PUT /api/v1/factors/{factorId}/status 并传 status=2
###### 返回更新成功，母因子自身 status 变为 2，不联动子因子或主题
#### 新挖库母因子更新为失效库成功
##### 对 status=1 母因子请求 PUT /api/v1/factors/{factorId}/status 并传 status=3
###### 返回更新成功，母因子自身 status 变为 3，不联动子因子或主题
#### 新挖库母因子更新为删除状态成功
##### 对 status=1 母因子请求 PUT /api/v1/factors/{factorId}/status 并传 status=4
###### 返回更新成功，母因子自身 status 变为 4，列表不再展示该母因子
#### 有效库母因子更新为失效库成功
##### 对 status=2 母因子请求 PUT /api/v1/factors/{factorId}/status 并传 status=3
###### 返回更新成功，母因子自身 status 变为 3
#### 失效库母因子更新为有效库成功
##### 对 status=3 母因子请求 PUT /api/v1/factors/{factorId}/status 并传 status=2
###### 返回更新成功，母因子自身 status 变为 2
#### 有效库母因子不能回到新挖库
##### 对 status=2 母因子请求 PUT /api/v1/factors/{factorId}/status 并传 status=1
###### 返回状态流转错误，母因子状态不变
#### 失效库母因子不能回到新挖库
##### 对 status=3 母因子请求 PUT /api/v1/factors/{factorId}/status 并传 status=1
###### 返回状态流转错误，母因子状态不变
#### 有效库或失效库母因子不能直接删除
##### 对 status=2 或 status=3 母因子请求 PUT /api/v1/factors/{factorId}/status 并传 status=4
###### 返回状态流转错误，母因子状态不变
#### 目标状态等于当前状态失败
##### 对母因子传入与当前状态相同的 status 请求 PUT /api/v1/factors/{factorId}/status
###### 返回无变更或状态流转错误，母因子状态不变
#### pending 审批存在时直接更新母因子状态失败
##### 对已有 pending 审批的母因子请求 PUT /api/v1/factors/{factorId}/status
###### 返回对象正在审批错误，不能绕过审批更新状态
### 母因子批量状态
#### 批量更新母因子状态全部成功
##### 使用多个可流转母因子请求 PUT /api/v1/factors/status/batch
###### 所有目标母因子自身状态更新成功，不联动子因子或主题
#### 批量更新母因子状态中途失败中断
##### 使用第一个可流转、第二个不可流转、后续可流转的母因子列表请求 PUT /api/v1/factors/status/batch
###### 第一个成功且不回滚，第二个失败，第二个之后不再执行
#### 批量更新母因子状态包含 pending 对象失败
##### 在批量列表中放入已有 pending 审批的母因子请求 PUT /api/v1/factors/status/batch
###### 执行到 pending 对象时失败并中断，后续对象不再执行
### 母因子图表
#### 查询新挖库母因子图表成功
##### 使用 type=new 请求 GET /api/v1/factors/graph
###### 返回按天统计的 points，total 等于 points 汇总，数据与 status=1 的数据库聚合一致
#### 查询有效库母因子图表成功
##### 使用 type=valid 请求 GET /api/v1/factors/graph
###### 返回按天统计的 points，total 等于 points 汇总，数据与 status=2 的数据库聚合一致
#### 查询失效库母因子图表成功
##### 使用 type=invalid 请求 GET /api/v1/factors/graph
###### 返回按天统计的 points，total 等于 points 汇总，数据与 status=3 的数据库聚合一致
#### 不传 type 默认查询新挖库图表
##### 不传 type 请求 GET /api/v1/factors/graph
###### 返回结果与 type=new 查询一致
#### 使用时间范围查询母因子图表
##### 使用 from 和 to 请求 GET /api/v1/factors/graph
###### 返回 period 均在时间范围内，数据与数据库按天聚合一致
#### 使用时间范围别名查询母因子图表
##### 使用 start/end、start_date/end_date 或 created_from/created_to 请求 GET /api/v1/factors/graph
###### 返回结果与 from/to 对应查询一致
### 母因子通知和复制
#### 首次通知同步挖掘结果成功
##### 使用 X-Webhook-Secret=dev-webhook-secret 和真实 run_id 请求 POST /api/v1/factors/notification
###### 返回处理成功，按 factor_mining_details 中 is_selected=true 的最新记录同步到 factors_details
#### 重复 run_id 通知跳过
##### 对同一个 run_id 再次请求 POST /api/v1/factors/notification
###### 接口返回已处理或跳过结果，不重复创建或覆盖不应变更的数据
#### 缺少 webhook secret 通知失败
##### 不传 X-Webhook-Secret 请求 POST /api/v1/factors/notification
###### 返回认证失败或权限错误，不处理通知数据
#### webhook secret 错误通知失败
##### 使用错误 X-Webhook-Secret 请求 POST /api/v1/factors/notification
###### 返回认证失败或权限错误，不处理通知数据
#### 复制母因子成功
##### 使用真实 factor_id 请求 POST /api/v1/factors/copy
###### 返回复制成功，新母因子 status=1，factor_name 和 cn_name 自动追加字符且不与原母因子重复
#### 复制不存在母因子失败
##### 使用不存在的 factor_id 请求 POST /api/v1/factors/copy
###### 返回 404 或明确错误，不返回 500
### 母因子筛选项和标准
#### 查询母因子筛选项成功
##### 使用 status=1、status=2、status=3 分别请求 GET /api/v1/factors/filter-options
###### 返回 creators、operators、mining_methods、strategies 去重数据，与数据库一致
#### 查询评估标准成功
##### 使用 time_window 和 coin_category 请求 GET /api/v1/factor-evaluation-standards
###### 返回评估标准成功，阈值数据与数据库一致
#### 查询币种池标的成功
##### 使用 universe_key 和 is_active 请求 GET /api/v1/coin-universe-symbols
###### 返回币种池标的成功，active 状态和标的列表与数据库一致
### 主题列表
#### 查询主题列表成功
##### 使用有效 token 请求 GET /api/v1/themes
###### 返回主题列表成功，包含 factor_count、sub_factor_count、max_level，统计数据与数据库一致
#### 按 theme_key 查询主题列表
##### 使用真实 theme_key 请求 GET /api/v1/themes
###### 返回主题均匹配 theme_key，数据与数据库一致
#### 按 theme_name 查询主题列表
##### 使用真实 theme_name 请求 GET /api/v1/themes
###### 返回主题均匹配 theme_name，数据与数据库一致
#### 按 status=2 查询有效主题
##### 使用 status=2 请求 GET /api/v1/themes
###### 返回主题状态均为 2，数据与数据库一致
#### 按 status=3 查询无效主题
##### 使用 status=3 请求 GET /api/v1/themes
###### 返回主题状态均为 3，数据与数据库一致
#### status=4 主题不展示
##### 使用 status=4 请求 GET /api/v1/themes
###### 返回空结果或明确不支持结果，列表不展示删除状态主题
### 主题创建
#### 创建主题成功
##### 使用 theme_key、theme_name、cn_name 请求 POST /api/v1/themes
###### 返回创建成功，新主题 status=2，有效主题数据与数据库一致
#### 缺少 theme_key 创建主题失败
##### 不传 theme_key 请求 POST /api/v1/themes
###### 返回参数错误，不创建主题，不返回 500
#### theme_key 重复创建主题失败
##### 使用已存在 theme_key 请求 POST /api/v1/themes
###### 返回重复错误，不创建重复主题
#### theme_name 重复创建主题失败
##### 使用已存在 theme_name 请求 POST /api/v1/themes
###### 返回重复错误，不创建重复主题
#### cn_name 重复创建主题失败
##### 使用已存在 cn_name 请求 POST /api/v1/themes
###### 返回重复错误，不创建重复主题
### 主题详情和更新
#### 查询主题详情成功
##### 使用真实 theme_id 请求 GET /api/v1/themes/{themeId}
###### 返回 ThemeWithFactors 结构，主题、母因子、子因子关联数据与数据库一致
#### 查询不存在主题失败
##### 使用不存在的 theme_id 请求 GET /api/v1/themes/{themeId}
###### 返回 404 或明确错误，不返回 500
#### 更新主题名称成功
##### 对没有 pending 审批的主题请求 PUT /api/v1/themes/{themeId} 修改 theme_name 或 cn_name
###### 返回更新成功，主题名称立即生效并与数据库一致
#### 更新主题标签成功
##### 对没有 pending 审批的主题请求 PUT /api/v1/themes/{themeId} 修改 theme_tags
###### 返回更新成功，主题标签立即生效并与数据库一致
#### 修改 theme_key 场景不覆盖
##### 不设计修改 theme_key 的业务用例
###### theme_key 作为稳定标识，不作为本轮可修改字段验证
#### 更新 theme_name 重复失败
##### 将主题 theme_name 修改为已存在名称请求 PUT /api/v1/themes/{themeId}
###### 返回重复错误，原主题数据不变
#### 更新 cn_name 重复失败
##### 将主题 cn_name 修改为已存在中文名请求 PUT /api/v1/themes/{themeId}
###### 返回重复错误，原主题数据不变
#### 主题无变更更新失败
##### 使用与当前主题完全一致的数据请求 PUT /api/v1/themes/{themeId}
###### 返回无变更或参数错误，主题数据不变
#### pending 审批存在时直接更新主题失败
##### 对已有 pending 审批的主题请求 PUT /api/v1/themes/{themeId}
###### 返回对象正在审批错误，不能绕过审批直接更新
### 主题状态
#### 有效主题更新为无效成功
##### 对 status=2 主题请求 PUT /api/v1/themes/{themeId}/status 并传 status=3
###### 返回更新成功，只修改主题自身状态，不联动母因子或子因子
#### 无效主题更新为有效成功
##### 对 status=3 主题请求 PUT /api/v1/themes/{themeId}/status 并传 status=2
###### 返回更新成功，只修改主题自身状态，不联动母因子或子因子
#### 主题状态不支持 0
##### 请求 PUT /api/v1/themes/{themeId}/status 并传 status=0
###### 返回参数错误，主题状态不变
#### 主题状态不支持 1
##### 请求 PUT /api/v1/themes/{themeId}/status 并传 status=1
###### 返回参数错误，主题状态不变
#### 主题状态不支持 4
##### 请求 PUT /api/v1/themes/{themeId}/status 并传 status=4
###### 返回参数错误，主题状态不变
#### 主题目标状态等于当前状态失败
##### 对主题传入与当前状态相同的 status 请求 PUT /api/v1/themes/{themeId}/status
###### 返回无变更或参数错误，主题状态不变
#### pending 审批存在时直接更新主题状态失败
##### 对已有 pending 审批的主题请求 PUT /api/v1/themes/{themeId}/status
###### 返回对象正在审批错误，不能绕过审批更新状态
### 主题树
#### 查询主题树成功
##### 使用有效 token 请求 GET /api/v1/factors/theme-tree
###### 返回主题、母因子、子因子三层结构，关联关系与数据库一致
#### 主题树展示新挖库数据
##### 查询 GET /api/v1/factors/theme-tree 并检查 status=1 的母因子或子因子
###### 新挖库数据可出现在主题树中
#### 主题树展示有效库数据
##### 查询 GET /api/v1/factors/theme-tree 并检查 status=2 的母因子或子因子
###### 有效库数据可出现在主题树中
#### 主题树展示失效库数据
##### 查询 GET /api/v1/factors/theme-tree 并检查 status=3 的母因子或子因子
###### 失效库数据可出现在主题树中
#### 主题树不展示删除状态数据
##### 查询 GET /api/v1/factors/theme-tree 并检查 status=4 数据
###### 删除状态主题、母因子、子因子不在主题树展示范围内
### 子因子列表
#### 默认分页查询子因子列表成功
##### 使用有效 token 请求 GET /api/v1/sub-factors
###### 返回 HTTP 200，success=true，分页结构完整，默认 limit 为 50 或符合接口文档默认规则
#### 指定 page 和 limit 查询子因子列表
##### 使用 page=1、limit=5 请求 GET /api/v1/sub-factors
###### 返回 items 数量不超过 5，分页 total 与数据库只读查询一致
#### 按 sub_factor_name 模糊查询子因子
##### 使用真实名称关键字请求 GET /api/v1/sub-factors
###### 返回子因子名称包含关键字，分页 total 与数据库一致
#### 按 factor_id 查询子因子
##### 使用真实 factor_id 请求 GET /api/v1/sub-factors
###### 返回子因子均关联该母因子，数据与数据库一致
#### 按 status=1 查询新挖库子因子
##### 使用 status=1 请求 GET /api/v1/sub-factors
###### 返回子因子的详情状态均为 1，接口数据与数据库一致
#### 按 status=2 查询有效库子因子
##### 使用 status=2 请求 GET /api/v1/sub-factors
###### 返回子因子的详情状态均为 2，接口数据与数据库一致
#### 按 status=3 查询失效库子因子
##### 使用 status=3 请求 GET /api/v1/sub-factors
###### 返回子因子的详情状态均为 3，接口数据与数据库一致
#### status=4 子因子不展示
##### 使用 status=4 请求 GET /api/v1/sub-factors
###### 返回空结果或明确不支持结果，列表不展示删除状态子因子
#### status 与 factor_detail_status 同值查询一致
##### 分别使用 status=2 和 factor_detail_status=2 请求 GET /api/v1/sub-factors
###### 两种筛选结果中的子因子详情状态一致，分页 total 与数据库一致
#### 按 time_window 筛选子因子列表
##### 使用真实 time_window 请求 GET /api/v1/sub-factors
###### 返回子因子的窗口指标与筛选窗口一致，接口数据与数据库一致
#### 按 created_by 筛选子因子列表
##### 使用真实 created_by 请求 GET /api/v1/sub-factors
###### 返回子因子的创建人均符合筛选条件，分页 total 与数据库一致
#### 按创建时间范围筛选子因子列表
##### 使用 created_from 和 created_to 请求 GET /api/v1/sub-factors
###### 返回子因子的创建时间均在范围内，分页 total 与数据库一致
#### 按 operator_by 筛选子因子列表
##### 使用真实 operator_by 请求 GET /api/v1/sub-factors
###### 返回子因子的操作人均符合筛选条件，分页 total 与数据库一致
#### 按操作时间范围筛选子因子列表
##### 使用 operated_from 和 operated_to 请求 GET /api/v1/sub-factors
###### 返回子因子的操作时间均在范围内，分页 total 与数据库一致
#### 按 updated_at 升序查询子因子列表
##### 使用 sort_by=updated_at、sort_order=asc 请求 GET /api/v1/sub-factors
###### 返回数据按 updated_at 升序排列，与数据库排序一致
#### 按 updated_at 降序查询子因子列表
##### 使用 sort_by=updated_at、sort_order=desc 请求 GET /api/v1/sub-factors
###### 返回数据按 updated_at 降序排列，与数据库排序一致
#### 未带 token 查询子因子列表失败
##### 不带 Authorization 请求 GET /api/v1/sub-factors
###### 返回 401 或 403，不返回子因子列表
### 子因子创建
#### 创建二级子因子成功
##### 使用 level=2、factor_id 或 factor_ids 请求 POST /api/v1/sub-factors
###### 返回创建成功，新子因子 status=1，并关联指定母因子
#### 创建多母因子关联的二级子因子成功
##### 使用 level=2 和多个 factor_ids 请求 POST /api/v1/sub-factors
###### 返回创建成功，子因子同时关联多个母因子
#### level=2 缺少母因子失败
##### 使用 level=2 但不传 factor_id 或 factor_ids 请求 POST /api/v1/sub-factors
###### 返回参数错误，不创建子因子
#### level=2 传 parent_sub_factor_ids 失败
##### 使用 level=2 且传 parent_sub_factor_ids 请求 POST /api/v1/sub-factors
###### 返回参数错误，不创建子因子
#### 创建高层级子因子成功
##### 使用 level>2、parent_sub_factor_ids 请求 POST /api/v1/sub-factors
###### 返回创建成功，新子因子的父级为指定子因子
#### level 大于 2 缺少 parent_sub_factor_ids 失败
##### 使用 level>2 但不传 parent_sub_factor_ids 请求 POST /api/v1/sub-factors
###### 返回参数错误，不创建子因子
#### level 大于 2 时 factor_ids 必须是父级已关联母因子子集
##### 使用 level>2、parent_sub_factor_ids 和超出父级关联范围的 factor_ids 请求 POST /api/v1/sub-factors
###### 返回参数错误，不创建子因子
#### 多个父子因子必须同层级
##### 使用不同 level 的 parent_sub_factor_ids 请求 POST /api/v1/sub-factors
###### 返回参数错误，不创建子因子
#### 子因子层级必须是父级层级加一
##### 使用与父级 level 不匹配的 level 请求 POST /api/v1/sub-factors
###### 返回参数错误，不创建子因子
#### 循环依赖创建允许
##### 使用可形成循环依赖的父级关系请求 POST /api/v1/sub-factors
###### 接口允许提交并返回成功，关系按请求内容保存
#### 缺少 serial_prefix 创建子因子失败
##### 不传 serial_prefix 请求 POST /api/v1/sub-factors
###### 返回参数错误，不创建子因子，不返回 500
#### 缺少 sub_factor_name 创建子因子失败
##### 不传 sub_factor_name 请求 POST /api/v1/sub-factors
###### 返回参数错误，不创建子因子，不返回 500
#### sub_factor_name 重复创建失败
##### 使用已存在 sub_factor_name 请求 POST /api/v1/sub-factors
###### 返回重复错误，不创建重复子因子
#### cn_name 重复创建子因子失败
##### 使用已存在 cn_name 请求 POST /api/v1/sub-factors
###### 返回重复错误，不创建重复子因子
#### 请求中传 status 不改变默认新挖状态
##### 创建子因子时额外传 status=2 或 status=3 请求 POST /api/v1/sub-factors
###### 创建成功时仍为 status=1，或返回参数错误，不允许创建到有效库或失效库
### 子因子汇总和图表
#### 查询新挖库子因子汇总成功
##### 使用 type=new 请求 GET /api/v1/sub-factors/summary
###### 返回汇总分页成功，列表数据与 status=1 的数据库基础查询一致
#### 查询有效库子因子汇总成功
##### 使用 type=valid 请求 GET /api/v1/sub-factors/summary
###### 返回汇总分页成功，列表数据与 status=2 的数据库基础查询一致
#### 查询失效库子因子汇总成功
##### 使用 type=invalid 请求 GET /api/v1/sub-factors/summary
###### 返回汇总分页成功，列表数据与 status=3 的数据库基础查询一致
#### 子因子汇总分页和排序成功
##### 使用 page、limit、sort_by、sort_order 请求 GET /api/v1/sub-factors/summary
###### 返回分页和排序结构正确，不校验具体 IC 窗口指标公式
#### 查询新挖库子因子图表成功
##### 使用 type=new 请求 GET /api/v1/sub-factors/graph
###### 返回按天统计的 points，total 等于 points 汇总，数据与 status=1 的数据库聚合一致
#### 查询有效库子因子图表成功
##### 使用 type=valid 请求 GET /api/v1/sub-factors/graph
###### 返回按天统计的 points，total 等于 points 汇总，数据与 status=2 的数据库聚合一致
#### 查询失效库子因子图表成功
##### 使用 type=invalid 请求 GET /api/v1/sub-factors/graph
###### 返回按天统计的 points，total 等于 points 汇总，数据与 status=3 的数据库聚合一致
#### 查询子因子最早日期成功
##### 请求 GET /api/v1/sub-factors/earliest-date
###### 返回最早 created_at 对应日期，格式为 YYYY-MM-DD，与数据库一致
### 子因子详情和更新
#### 查询子因子详情成功
##### 使用真实 subFactorId 请求 GET /api/v1/sub-factors/{subFactorId}
###### 返回子因子详情成功，父级关系、母因子关联、status 与数据库一致
#### 查询不存在子因子失败
##### 使用不存在的 subFactorId 请求 GET /api/v1/sub-factors/{subFactorId}
###### 返回 404 或明确错误，不返回 500
#### 更新子因子基础信息成功
##### 对没有 pending 审批的子因子请求 PUT /api/v1/sub-factors/{subFactorId} 修改名称、中文名、公式或标签
###### 返回更新成功，子因子数据立即生效并与数据库一致
#### 更新子因子父级关系成功
##### 按 level 规则修改 factor_ids 或 parent_sub_factor_ids 请求 PUT /api/v1/sub-factors/{subFactorId}
###### 返回更新成功，父级关系和母因子关联与数据库一致
#### 更新子因子到多母因子关联成功
##### 将子因子 factor_ids 修改为多个母因子请求 PUT /api/v1/sub-factors/{subFactorId}
###### 返回更新成功，子因子同时关联多个母因子
#### 更新 sub_factor_name 重复失败
##### 将子因子 sub_factor_name 修改为已存在名称请求 PUT /api/v1/sub-factors/{subFactorId}
###### 返回重复错误，原子因子数据不变
#### 更新 cn_name 重复失败
##### 将子因子 cn_name 修改为已存在中文名请求 PUT /api/v1/sub-factors/{subFactorId}
###### 返回重复错误，原子因子数据不变
#### 无变更更新子因子失败
##### 使用与当前子因子完全一致的数据请求 PUT /api/v1/sub-factors/{subFactorId}
###### 返回无变更或参数错误，子因子数据不变
#### pending 审批存在时直接更新子因子失败
##### 对已有 pending 审批的子因子请求 PUT /api/v1/sub-factors/{subFactorId}
###### 返回对象正在审批错误，不能绕过审批直接更新
### 子因子状态
#### 新挖库子因子更新为有效库成功
##### 对 status=1 子因子请求 PUT /api/v1/sub-factors/{subFactorId}/status 并传 status=2
###### 返回更新成功，只修改子因子自身状态，不联动母因子或主题
#### 新挖库子因子更新为失效库成功
##### 对 status=1 子因子请求 PUT /api/v1/sub-factors/{subFactorId}/status 并传 status=3
###### 返回更新成功，只修改子因子自身状态，不联动母因子或主题
#### 新挖库子因子更新为删除状态成功
##### 对 status=1 子因子请求 PUT /api/v1/sub-factors/{subFactorId}/status 并传 status=4
###### 返回更新成功，只修改子因子自身状态，列表不再展示该子因子
#### 有效库子因子更新为失效库成功
##### 对 status=2 子因子请求 PUT /api/v1/sub-factors/{subFactorId}/status 并传 status=3
###### 返回更新成功，子因子自身 status 变为 3
#### 失效库子因子更新为有效库成功
##### 对 status=3 子因子请求 PUT /api/v1/sub-factors/{subFactorId}/status 并传 status=2
###### 返回更新成功，子因子自身 status 变为 2
#### 有效库子因子不能回到新挖库
##### 对 status=2 子因子请求 PUT /api/v1/sub-factors/{subFactorId}/status 并传 status=1
###### 返回状态流转错误，子因子状态不变
#### 失效库子因子不能回到新挖库
##### 对 status=3 子因子请求 PUT /api/v1/sub-factors/{subFactorId}/status 并传 status=1
###### 返回状态流转错误，子因子状态不变
#### 有效库或失效库子因子不能直接删除
##### 对 status=2 或 status=3 子因子请求 PUT /api/v1/sub-factors/{subFactorId}/status 并传 status=4
###### 返回状态流转错误，子因子状态不变
#### 目标状态等于当前状态失败
##### 对子因子传入与当前状态相同的 status 请求 PUT /api/v1/sub-factors/{subFactorId}/status
###### 返回无变更或状态流转错误，子因子状态不变
#### pending 审批存在时直接更新子因子状态失败
##### 对已有 pending 审批的子因子请求 PUT /api/v1/sub-factors/{subFactorId}/status
###### 返回对象正在审批错误，不能绕过审批更新状态
### 子因子批量状态和复制
#### 批量更新子因子状态全部成功
##### 使用多个可流转子因子请求 PUT /api/v1/sub-factors/status/batch
###### 所有目标子因子自身状态更新成功，不联动母因子或主题
#### 批量更新子因子状态中途失败中断
##### 使用第一个可流转、第二个不可流转、后续可流转的子因子列表请求 PUT /api/v1/sub-factors/status/batch
###### 第一个成功且不回滚，第二个失败，第二个之后不再执行
#### 批量更新子因子状态包含 pending 对象失败
##### 在批量列表中放入已有 pending 审批的子因子请求 PUT /api/v1/sub-factors/status/batch
###### 执行到 pending 对象时失败并中断，后续对象不再执行
#### 复制子因子成功
##### 使用真实 subFactorId 请求 POST /api/v1/sub-factors/copy
###### 返回复制成功，新子因子 status=1，sub_factor_name 和 cn_name 自动追加字符且不与原子因子重复
#### 复制不存在子因子失败
##### 使用不存在的 subFactorId 请求 POST /api/v1/sub-factors/copy
###### 返回 404 或明确错误，不返回 500
### 子因子筛选项
#### 查询子因子筛选项成功
##### 使用 status=1、status=2、status=3 分别请求 GET /api/v1/sub-factors/filter-options
###### 返回 creators、operators 去重数据，与数据库一致
## FactorIC
### 母因子 IC
#### 查询母因子切片指标成功
##### 使用真实 factor_id 请求 GET /api/v1/factor-ic/factors/{factor_id}/slice-metrics
###### 返回母因子切片指标成功，is_sub_factor_id=false，数据与 factor_ic_slice_metrics 对应记录一致
#### 查询母因子汇总指标成功
##### 使用真实 factor_id 请求 GET /api/v1/factor-ic/factors/{factor_id}/summary
###### 返回母因子汇总指标成功，is_sub_factor_id=false，数据与汇总指标表对应记录一致
#### 查询母因子按币种窗口指标成功
##### 使用真实 factor_id 请求 GET /api/v1/factor-ic/factors/{factor_id}/symbol-window-metrics
###### 返回母因子按币种和窗口的指标成功，数据与 factor_mining_symbol_window_metric 对应记录一致
#### 查询不存在母因子 IC 失败
##### 使用不存在的 factor_id 请求母因子 IC 相关接口
###### 返回 404 或空数据的明确结果，不返回 500
### 子因子 IC
#### 查询子因子切片指标成功
##### 使用真实 sub_factor_id 请求 GET /api/v1/factor-ic/sub-factors/{sub_factor_id}/slice-metrics
###### 返回子因子切片指标成功，is_sub_factor_id=true，数据与 factor_ic_slice_metrics 对应记录一致
#### 查询子因子汇总指标成功
##### 使用真实 sub_factor_id 请求 GET /api/v1/factor-ic/sub-factors/{sub_factor_id}/summary
###### 返回子因子汇总指标成功，is_sub_factor_id=true，数据与汇总指标表对应记录一致
#### 查询子因子按币种窗口指标成功
##### 使用真实 sub_factor_id 请求 GET /api/v1/factor-ic/sub-factors/{sub_factor_id}/symbol-window-metrics
###### 返回子因子按币种和窗口的指标成功，数据与 factor_mining_symbol_window_metric 对应记录一致
#### 查询不存在子因子 IC 失败
##### 使用不存在的 sub_factor_id 请求子因子 IC 相关接口
###### 返回 404 或空数据的明确结果，不返回 500
### 通用切片指标
#### 查询切片指标列表成功
##### 使用接口文档支持的筛选参数请求 GET /api/v1/factor-ic/slice-metrics
###### 返回切片指标列表成功，分页、筛选和字段数据与 factor_ic_slice_metrics 一致
#### 按母因子标识查询切片指标
##### 使用 factor_id 和 is_sub_factor_id=false 请求 GET /api/v1/factor-ic/slice-metrics
###### 返回记录均属于该母因子，数据与数据库一致
#### 按子因子标识查询切片指标
##### 使用 sub_factor_id 和 is_sub_factor_id=true 请求 GET /api/v1/factor-ic/slice-metrics
###### 返回记录均属于该子因子，数据与数据库一致
#### 按窗口和币种查询切片指标
##### 使用 time_window 和 symbol 请求 GET /api/v1/factor-ic/slice-metrics
###### 返回记录均符合窗口和币种筛选条件，数据与数据库一致
### 批量写入指标
#### 批量写入切片指标成功
##### 使用有效切片指标数组请求 POST /api/v1/factor-ic/slice-metrics/batch
###### 返回写入成功，新增记录可通过查询接口和数据库只读查询找到
#### 批量写入切片指标重复唯一键更新成功
##### 使用相同唯一键再次请求 POST /api/v1/factor-ic/slice-metrics/batch 并修改指标值
###### 返回写入成功，不新增重复记录，原记录指标值被更新
#### 批量写入切片指标空数组失败
##### 使用空数组请求 POST /api/v1/factor-ic/slice-metrics/batch
###### 返回参数错误，不写入数据，不返回 500
#### 批量写入切片指标缺少必填字段失败
##### 删除接口文档要求的必填字段请求 POST /api/v1/factor-ic/slice-metrics/batch
###### 返回参数错误，不写入数据，不返回 500
#### 批量写入汇总指标成功
##### 使用有效汇总指标数组请求 POST /api/v1/factor-ic/summary-metrics/batch
###### 返回写入成功，新增记录可通过查询接口和数据库只读查询找到
#### 批量写入汇总指标重复唯一键更新成功
##### 使用相同唯一键再次请求 POST /api/v1/factor-ic/summary-metrics/batch 并修改指标值
###### 返回写入成功，不新增重复记录，原记录指标值被更新
#### 批量写入汇总指标空数组失败
##### 使用空数组请求 POST /api/v1/factor-ic/summary-metrics/batch
###### 返回参数错误，不写入数据，不返回 500
#### 批量写入汇总指标缺少必填字段失败
##### 删除接口文档要求的必填字段请求 POST /api/v1/factor-ic/summary-metrics/batch
###### 返回参数错误，不写入数据，不返回 500
### IC 运行记录
#### 查询 IC 运行记录列表成功
##### 使用有效 token 请求 GET /api/v1/factor-ic/runs
###### 返回运行记录列表成功，分页结构和字段数据与数据库一致
#### 创建 IC 运行记录成功
##### 使用接口文档要求的参数请求 POST /api/v1/factor-ic/runs
###### 返回创建成功，运行记录可通过列表和数据库只读查询找到
#### 查询 IC 运行记录详情成功
##### 使用真实 run_id 请求 GET /api/v1/factor-ic/runs/{run_id}
###### 返回运行记录详情成功，字段数据与数据库一致
#### 查询不存在 IC 运行记录失败
##### 使用不存在的 run_id 请求 GET /api/v1/factor-ic/runs/{run_id}
###### 返回 404 或明确错误，不返回 500
### 评分标准
#### 查询 IC 评分标准成功
##### 使用 time_window 和 coin_category 请求 GET /api/v1/factor-ic/scoring-standards
###### 返回评分标准成功，阈值数据与数据库一致
#### 查询不存在条件的评分标准返回明确结果
##### 使用不存在的 time_window 或 coin_category 请求 GET /api/v1/factor-ic/scoring-standards
###### 返回空结果或明确错误，不返回 500
### 认证
#### 未带 token 查询 FactorIC 失败
##### 不带 Authorization 请求 FactorIC 查询接口
###### 返回 401 或 403，不返回指标数据
#### 无效 token 查询 FactorIC 失败
##### 使用伪造 token 请求 FactorIC 查询接口
###### 返回 401 或 403，不返回指标数据
## Admin
### 管理员用户
#### 查询管理员列表成功
##### 使用超级管理员 token 请求 GET /api/v1/admin/admins 或用户列表接口
###### 返回管理员分页列表成功，用户状态、角色、邮箱与数据库一致
#### 创建 admin 用户成功
##### 使用新邮箱、display_name、role=admin、有效密码请求 POST /api/v1/admin/admins
###### 返回创建成功，新用户可查询到，角色为 admin
#### 创建 super_admin 用户成功
##### 使用新邮箱、display_name、role=super_admin、有效密码请求 POST /api/v1/admin/admins
###### 返回创建成功，新用户可查询到，角色为 super_admin
#### 创建管理员邮箱重复失败
##### 使用已存在邮箱请求 POST /api/v1/admin/admins
###### 返回重复错误，不创建重复用户
#### 创建管理员密码长度不足失败
##### 使用长度小于 8 位的密码请求 POST /api/v1/admin/admins
###### 返回参数错误，不创建用户，不返回 500
#### 查询管理员详情成功
##### 使用真实 admin_id 请求 GET /api/v1/admin/admins/{admin_id}
###### 返回管理员详情成功，字段数据与数据库一致
#### 更新管理员资料成功
##### 修改 display_name、role、status、notes 或 last_login_mac 请求 PATCH /api/v1/admin/admins/{admin_id}
###### 返回更新成功，字段数据与数据库一致
#### 重置管理员密码成功
##### 使用新密码请求 PATCH /api/v1/admin/admins/{admin_id}/password
###### 返回重置成功，新密码可登录，旧密码不可登录
#### 删除自动化创建管理员成功
##### 对自动化创建的管理员请求 DELETE /api/v1/admin/admins/{admin_id}
###### 返回删除成功，该用户不可再正常登录或查询为有效用户
#### 解锁管理员成功
##### 对已因 6 次失败登录被锁定的用户请求 POST /api/v1/admin/users/unlock
###### 返回解锁成功，该用户可使用正确密码登录
#### 解锁不存在用户返回明确结果
##### 使用不存在邮箱请求 POST /api/v1/admin/users/unlock
###### 返回成功或 404 等明确结果，不返回 500
### 用户权限
#### 查询权限定义列表成功
##### 使用超级管理员 token 请求 GET /api/v1/admin/permissions
###### 返回权限定义列表成功，权限 code、名称、分组与配置或数据库一致
#### 查询用户权限成功
##### 使用真实 user_id 请求 GET /api/v1/admin/users/{user_id}/permissions
###### 返回用户权限成功，权限集合与数据库或权限配置一致
#### 替换普通管理员权限成功
##### 对普通 admin 用户请求 PUT /api/v1/admin/users/{user_id}/permissions
###### 返回替换成功，用户权限变为请求中的权限集合
#### 授予普通管理员权限成功
##### 对普通 admin 用户请求 POST /api/v1/admin/users/{user_id}/permissions/{code}
###### 返回授予成功，用户权限包含该 code
#### 撤销普通管理员权限成功
##### 对普通 admin 用户请求 DELETE /api/v1/admin/users/{user_id}/permissions/{code}
###### 返回撤销成功，用户权限不再包含该 code
#### 清空普通管理员权限成功
##### 对普通 admin 用户请求 PUT /api/v1/admin/users/{user_id}/permissions 并传空权限集合
###### 返回替换成功，用户权限为空或仅保留系统强制权限
#### super_admin 高风险权限清空不覆盖
##### 不对 super_admin 执行权限清空或全量替换为低权限集合
###### 避免破坏超级管理员权限，后续只覆盖查询类场景
### 角色模板
#### 查询角色模板列表成功
##### 使用超级管理员 token 请求 GET /api/v1/admin/role-templates
###### 返回角色模板列表成功，字段数据与数据库或配置一致
#### 创建角色模板成功
##### 使用自动化 role_name、display_name、权限集合请求 POST /api/v1/admin/role-templates
###### 返回创建成功，角色模板可查询到
#### 创建重复 role_name 角色模板失败
##### 使用已存在 role_name 请求 POST /api/v1/admin/role-templates
###### 返回重复错误，不创建重复模板
#### 查询角色模板详情成功
##### 使用真实 role_name 请求 GET /api/v1/admin/role-templates/{role_name}
###### 返回角色模板详情成功，权限集合和显示字段正确
#### 更新角色模板成功
##### 对自动化创建的角色模板请求 PATCH /api/v1/admin/role-templates/{role_name}
###### 返回更新成功，display_name、description、权限集合按请求生效
#### can_edit=false 角色模板不允许更新
##### 对 can_edit=false 的角色模板请求 PATCH /api/v1/admin/role-templates/{role_name}
###### 返回权限或业务规则错误，模板数据不变
#### 查询角色模板权限名称成功
##### 使用真实 role_name 请求 GET /api/v1/admin/role-templates/{role_name}/permission-names
###### 返回权限显示名成功，每个权限 code 都有对应名称或明确空值
#### 删除自动化创建角色模板成功
##### 对自动化创建的角色模板请求 DELETE /api/v1/admin/role-templates/{role_name}
###### 返回删除成功，再次查询该模板不存在
#### can_delete=false 角色模板不允许删除
##### 对 can_delete=false 的角色模板请求 DELETE /api/v1/admin/role-templates/{role_name}
###### 返回权限或业务规则错误，模板仍存在
### 量化账户
#### 查询量化账户列表成功
##### 使用管理员 token 请求 GET /api/v1/admin/quant-accounts
###### 返回量化账户分页列表成功，字段数据与数据库一致
#### 按 exchange 查询量化账户列表
##### 使用 Binance、OKX、Bitget、Kucoin、Bybit 或 Huobi 请求 GET /api/v1/admin/quant-accounts
###### 返回账户交易所符合筛选条件，分页 total 与数据库一致
#### 按 admin_id 查询量化账户列表
##### 使用真实 admin_id 请求 GET /api/v1/admin/quant-accounts
###### 返回账户均属于该管理员，数据与数据库一致
#### 按 status 查询量化账户列表
##### 使用 active、inactive、expired 请求 GET /api/v1/admin/quant-accounts
###### 返回账户状态符合筛选条件，数据与数据库一致
#### 按邮箱或说明搜索量化账户
##### 使用 search 参数请求 GET /api/v1/admin/quant-accounts
###### 返回账户邮箱或说明匹配搜索关键字，数据与数据库一致
#### 创建 Binance 量化账户成功
##### 使用 exchange=Binance、email、api_key、secret_key 请求 POST /api/v1/admin/quant-accounts
###### 返回创建成功，账户可查询到
#### 创建 OKX 量化账户成功
##### 使用 exchange=OKX、email、api_key、secret_key、api_password 请求 POST /api/v1/admin/quant-accounts
###### 返回创建成功，账户可查询到
#### 创建 Bitget 量化账户成功
##### 使用 exchange=Bitget、email、api_key、secret_key、api_password 请求 POST /api/v1/admin/quant-accounts
###### 返回创建成功，账户可查询到
#### 创建 Kucoin 量化账户成功
##### 使用 exchange=Kucoin、email、api_key、secret_key、api_password 请求 POST /api/v1/admin/quant-accounts
###### 返回创建成功，账户可查询到
#### 创建 Bybit 量化账户成功
##### 使用 exchange=Bybit、email、api_key、secret_key 请求 POST /api/v1/admin/quant-accounts
###### 返回创建成功，账户可查询到
#### OKX 缺少 api_password 创建失败
##### 使用 exchange=OKX 但不传 api_password 请求 POST /api/v1/admin/quant-accounts
###### 返回参数错误，不创建账户
#### Bitget 缺少 api_password 创建失败
##### 使用 exchange=Bitget 但不传 api_password 请求 POST /api/v1/admin/quant-accounts
###### 返回参数错误，不创建账户
#### Kucoin 缺少 api_password 创建失败
##### 使用 exchange=Kucoin 但不传 api_password 请求 POST /api/v1/admin/quant-accounts
###### 返回参数错误，不创建账户
#### 重复邮箱交易所和 key 创建允许
##### 使用完全相同的 exchange、email、api_key、secret_key 再次请求 POST /api/v1/admin/quant-accounts
###### 返回创建成功或接口文档允许的结果，不按唯一性冲突设计失败用例
#### 查询量化账户详情成功
##### 使用真实 account_id 请求 GET /api/v1/admin/quant-accounts/{account_id}
###### 返回账户详情成功，api_key 明文返回，secret_key 脱敏，api_password 有值时返回星号
#### 更新量化账户成功
##### 修改 email、api_description、status 或密钥信息请求 PATCH /api/v1/admin/quant-accounts/{account_id}
###### 返回更新成功，字段数据与数据库一致
#### 删除自动化创建量化账户成功
##### 对自动化创建账户请求 DELETE /api/v1/admin/quant-accounts/{account_id}
###### 返回删除成功，再次查询该账户不存在或状态为删除
#### 查询存储账户实时信息成功
##### 对已创建且凭证有效的账户请求 GET /api/v1/admin/quant-accounts/{account_id}/account-info
###### 返回实时账户信息成功，account_type=spot、futures、all 的结果结构符合接口文档
#### 使用错误凭证查询存储账户实时信息失败
##### 对凭证错误的账户请求 GET /api/v1/admin/quant-accounts/{account_id}/account-info
###### 返回交易所连接失败或 502，不返回成功账户资产
#### 直连交易所账户查询成功
##### 使用有效 exchange、api_key、secret_key、api_password 请求 POST /api/v1/admin/exchange/account
###### 返回交易所账户信息成功，资产结构符合接口文档
#### 直连交易所账户 account_type 非法失败
##### 使用非法 account_type 请求 POST /api/v1/admin/exchange/account
###### 返回参数错误，不请求或不返回成功资产
#### 直连交易所账户凭证错误失败
##### 使用错误 api_key 或 secret_key 请求 POST /api/v1/admin/exchange/account
###### 返回交易所连接失败或 502，不返回成功账户资产
### 因子评估标准管理
#### 查询因子评估标准列表成功
##### 使用管理员 token 请求 GET /api/v1/admin/factor-evaluation-standards
###### 返回评估标准列表成功，数据与数据库一致
#### 创建因子评估标准成功
##### 使用自动化 time_window、coin_category 和阈值请求 POST /api/v1/admin/factor-evaluation-standards
###### 返回创建成功，标准可通过查询接口和数据库找到
#### 重复 time_window 和 coin_category 创建失败
##### 使用已存在 time_window 和 coin_category 请求 POST /api/v1/admin/factor-evaluation-standards
###### 返回重复错误，不创建重复标准
#### 更新因子评估标准成功
##### 对自动化创建标准请求 PATCH /api/v1/admin/factor-evaluation-standards/{id}
###### 返回更新成功，阈值字段按请求生效
#### 删除自动化创建因子评估标准成功
##### 对自动化创建标准请求 DELETE /api/v1/admin/factor-evaluation-standards/{id}
###### 返回删除成功，再次查询该标准不存在
### 连贯场景
#### 管理员创建重置密码登录删除链路
##### 创建管理员、重置密码、使用新密码登录、删除该管理员
###### 链路内每个接口均成功，删除后该管理员不可继续登录
#### 角色模板创建更新权限名删除链路
##### 创建角色模板、查询详情、更新权限、查询权限名称、删除模板
###### 链路内每个接口均成功，删除后模板不存在
#### 普通管理员权限授予撤销链路
##### 创建普通管理员、授予权限、查询权限、撤销权限、清理用户
###### 权限集合变化符合每次操作结果
#### 量化账户创建查询更新实时信息删除链路
##### 创建量化账户、查询列表和详情、更新说明、查询实时信息、删除账户
###### 链路内每个接口均成功或实时信息按凭证返回明确结果，账户最终被清理
## 覆盖边界
### 本轮暂不覆盖
#### Quantitative Trading 策略接口暂不覆盖
##### 暂不设计 /api/v1/quantitative-trading 下策略、资金、绩效相关用例
###### 等策略公式和业务规则确认后再补充
#### Chat 接口暂不覆盖
##### 暂不设计 Chat 对话、webhook、挖掘触发相关用例
###### 等整体逻辑确认后再补充
#### Runs 接口暂不覆盖
##### 暂不设计 Runs 运行任务相关用例
###### 等运行任务业务链路确认后再补充
#### 子因子刷新接口暂不覆盖
##### 暂不设计 sub-factors refresh 相关用例
###### 该能力依赖 Chat、gateway、rd_agent 或 webhook，后续单独确认
#### FactorIC 旧接口暂不覆盖
##### 暂不设计 factor-ic by-symbol 和旧 summary-metrics 查询接口用例
###### 旧接口不纳入新版自动化覆盖范围
#### 邀请码接口暂不覆盖
##### 暂不设计邀请码相关接口和参数用例
###### 当前业务没有邀请码流程
#### 提示词和挖掘配置接口暂不覆盖
##### 暂不设计 Prompts、agent-factory-config 相关用例
###### 这类接口属于挖掘配置，后续确认后再补充
#### Docs 和 System 接口暂不覆盖
##### 暂不设计文档和系统健康类接口用例
###### 当前自动化重点是业务接口和 DB 对账
