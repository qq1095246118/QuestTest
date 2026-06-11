# 因子库接口用例
## Auth
### 登录
#### AU-01 有效账号登录成功
##### 使用配置中的邮箱和密码请求 login，POST /api/v1/auth/login
###### 返回 HTTP 200，success=true，data.token 不为空，data.user.email 与登录账号一致
#### AU-02 错误密码登录失败
##### 使用正确邮箱和错误密码请求 login，POST /api/v1/auth/login
###### 返回 400、401 或 403，响应内容不包含 token
#### AU-03 缺少邮箱登录失败
##### email 传空字符串并使用有效密码请求 login，POST /api/v1/auth/login
###### 返回 400、401 或 422，不返回 500
#### AU-04 非法邮箱格式登录失败
##### 使用 not-an-email 和任意密码请求 login，POST /api/v1/auth/login
###### 返回 400、401 或 422，不返回 500
### 注册
#### AU-05 缺少密码注册失败
##### 使用 auto 邮箱、空 password、auto display_name 请求 register，POST /api/v1/auth/register
###### 返回 400、401、409 或 422，不返回 500
#### AU-06 已存在邮箱注册失败
##### 使用已存在管理员邮箱请求 register，POST /api/v1/auth/register
###### 返回 400、401、409 或 422，不返回 500
### 当前用户
#### AU-07 未带 token 查询当前用户失败
##### 不带有效 Authorization 请求当前用户资料，GET /api/v1/me
###### 返回 401 或 403
#### AU-08 有效 token 查询当前用户成功
##### 使用登录返回 token 请求当前用户资料，GET /api/v1/me
###### 返回 HTTP 200，success=true，data.email 与登录邮箱一致
### 连贯场景
#### AS-01 登录后使用 token 查询当前用户资料
##### 先请求 login 获取 token，再使用 token 请求 /api/v1/me
###### 登录响应 user.email 与当前用户资料 email 一致，两个接口均返回成功
## factor
### 因子列表
#### FA-01 默认第一页因子列表与 DB 一致
##### 使用有效 token 请求 listFactors，GET /api/v1/factors?page=1&limit=5
###### 返回 success=true，items 和 pagination 结构完整，接口当前页数据与 DB 查询结果一致
#### FA-03 第二页因子列表与第一页不重复且与 DB 一致
##### 先按 status=2 请求第一页，再按 status=2 请求第二页，GET /api/v1/factors?page=1&limit=5&status=2 和 page=2&limit=5&status=2
###### 第一页和第二页 id 不重复，返回因子详情状态均为 2，第二页分页和字段数据与 DB 一致
#### FA-05 按 updated_at 升序查询因子列表与 DB 一致
##### 请求 listFactors，GET /api/v1/factors?page=1&limit=5&status=1&sort_by=updated_at&sort_order=asc
###### 返回因子详情状态均为 1，数据顺序与 DB 按相同状态和排序查询结果一致
#### FA-06 按 updated_at 降序查询因子列表与 DB 一致
##### 请求 listFactors，GET /api/v1/factors?page=1&limit=5&sort_by=updated_at&sort_order=desc
###### 返回数据顺序与 DB 按相同排序查询结果一致
#### FA-07 按主题筛选因子列表与 DB 一致
##### 先从因子列表取可用 theme_key，再请求 GET /api/v1/factors?factor_theme={theme_key}
###### 返回因子均匹配该主题，接口分页和字段数据与 DB 一致
#### FA-08 按 factor_detail_status=1 筛选因子列表与 DB 一致
##### 请求 listFactors，GET /api/v1/factors?page=1&limit=5&factor_detail_status=1
###### 返回因子详情状态符合筛选条件，接口数据与 DB 一致
#### FA-08B 按 status=1/2/3 筛选因子列表与 DB 一致
##### 分别请求 listFactors，GET /api/v1/factors?page=1&limit=5&status=1、status=2、status=3
###### 返回因子详情状态分别符合新挖库、有效库、失效库筛选条件，接口数据与 DB 一致
#### FA-09 未带 token 查询因子列表
##### 不带 Authorization 请求 listFactors，GET /api/v1/factors
###### 返回 401 或 403，不返回因子列表数据
#### FA-10 使用无效 token 查询因子列表
##### 使用伪造 Authorization 请求 listFactors，GET /api/v1/factors
###### 返回 401 或 403，不返回因子列表数据
#### FA-11 page=0 查询因子列表不返回 500
##### 请求 listFactors，GET /api/v1/factors?page=0&limit=5
###### 返回明确参数错误或自动修正后的合法分页结果，不返回 500
#### FA-12 limit=501 查询因子列表不返回 500
##### 请求 listFactors，GET /api/v1/factors?page=1&limit=501
###### 返回明确参数错误或自动限制后的合法分页结果，不返回 500
#### FA-13 sort_order=bad 查询因子列表不返回 500
##### 请求 listFactors，GET /api/v1/factors?sort_by=updated_at&sort_order=bad
###### 返回明确参数错误或默认排序结果，不返回 500
#### FA-14 因子列表主题存在于主题列表
##### 分别请求 listFactors 和 listThemes，GET /api/v1/factors 与 GET /api/v1/themes
###### 因子列表中每个 theme_id 都能在主题列表中找到
### 因子管理
#### FA-15 查询因子详情成功
##### 从因子列表派生真实 factor_id，请求 GET /api/v1/factors/{factor_id}
###### 返回 HTTP 200，success=true，data 为因子详情
#### FA-16 查询不存在因子失败
##### 使用 factor_id=999999999 请求 GET /api/v1/factors/{factor_id}
###### 返回 400、404 或 422
#### FA-17 创建因子成功
##### 使用 auto factor_name、cn_name、theme_id 请求 POST /api/v1/factors
###### 返回 success=true，data.id 不为空，创建出的因子登记清理
#### FA-18 缺少 factor_name 创建因子失败
##### 仅传 serial_prefix 请求 POST /api/v1/factors
###### 返回 400、401、403、409 或 422，不返回 500
#### FA-19 重复 factor_name 创建因子失败
##### 连续两次使用相同 factor_name 请求 POST /api/v1/factors
###### 第一次创建成功，第二次返回 400、409 或 422
#### FA-20 更新因子成功
##### 先创建 auto 因子，再请求 PUT /api/v1/factors/{factor_id} 更新 cn_name
###### 更新接口返回 success=true
#### FA-21 更新不存在因子失败
##### 使用 factor_id=999999999 请求 PUT /api/v1/factors/{factor_id}
###### 返回 400、404 或 422
#### FA-22 更新因子状态成功
##### 先创建 auto 因子，再请求 PUT /api/v1/factors/{factor_id}/status 将状态更新为 3
###### 状态更新接口返回 success=true，data.factor_detail.status 等于 3
#### FA-23 批量更新因子状态成功
##### 先创建 auto 因子，再请求 PUT /api/v1/factors/status/batch 批量更新状态
###### 批量状态更新接口返回 success=true，data.status 等于 3，updated_factor_ids 包含被更新因子
#### FA-24 复制因子成功
##### 先创建 auto 因子，再请求 POST /api/v1/factors/copy 复制该 factor_id
###### copy 接口返回 success=true，复制副本的 factor_detail.status 等于 1，副本保留后由人工或定时任务清理
#### FA-25 因子图表汇总查询成功
##### 使用有效 token 请求 GET /api/v1/factors/graph?type=new
###### 返回 success=true，data 结构完整
#### FA-26 因子筛选项查询成功
##### 使用有效 token 请求 GET /api/v1/factors/filter-options
###### 返回 success=true，data 结构完整
#### FA-27 未带 token 查询因子详情失败
##### 不带 Authorization 请求 GET /api/v1/factors/{factor_id}
###### 返回 401 或 403
### 主题
#### TH-01 主题列表查询成功
##### 使用有效 token 请求 GET /api/v1/themes
###### 返回 success=true，data 为主题列表或分页结构
#### TH-02 按 theme_key 查询主题列表成功
##### 先从主题列表取 theme_key，再请求 GET /api/v1/themes?theme_key={theme_key}
###### 返回主题数据匹配筛选条件
#### TH-03 未带 token 查询主题列表失败
##### 不带 Authorization 请求 GET /api/v1/themes
###### 返回 401 或 403
#### TH-04 创建主题成功
##### 使用 auto theme_key、theme_name、cn_name 请求 POST /api/v1/themes
###### 返回 success=true，data.id 不为空，创建出的主题登记清理
#### TH-05 缺少 theme_key 创建主题失败
##### 不传 theme_key 请求 POST /api/v1/themes
###### 返回 400、401、403、409 或 422，不返回 500
#### TH-06 重复 theme_key 创建主题失败
##### 连续两次使用相同 theme_key 请求 POST /api/v1/themes
###### 第一次创建成功，第二次返回 400、409 或 422
#### TH-07 查询主题详情成功
##### 从主题列表派生 theme_id，请求 GET /api/v1/themes/{theme_id}
###### 返回 success=true，data 为主题详情
#### TH-08 查询不存在主题失败
##### 使用 theme_id=999999999 请求 GET /api/v1/themes/{theme_id}
###### 返回 400、404 或 422
#### TH-09 更新主题成功
##### 先创建 auto 主题，再请求 PUT /api/v1/themes/{theme_id} 更新 cn_name
###### 更新接口返回 success=true
#### TH-10 更新不存在主题失败
##### 使用 theme_id=999999999 请求 PUT /api/v1/themes/{theme_id}
###### 返回 400、404 或 422
#### TH-11 更新主题状态成功
##### 先创建 auto 主题，再请求 PUT /api/v1/themes/{theme_id}/status 将状态更新为 3
###### 状态更新接口返回 success=true，data.status 等于 3
#### TH-12 非法主题状态更新失败
##### 使用非法 status 请求 PUT /api/v1/themes/{theme_id}/status
###### 返回 400、401、403 或 422，不返回 500
### 子因子
#### SF-01 子因子列表查询成功
##### 使用有效 token 请求 GET /api/v1/sub-factors?page=1&limit=5
###### 返回 success=true，items 和 pagination 结构完整
#### SF-02 按 factor_id 查询子因子列表成功
##### 从因子列表派生 factor_id，请求 GET /api/v1/sub-factors?factor_id={factor_id}
###### 返回 success=true，data 为子因子列表或分页结构
#### SF-02B 按 status=1/2/3 筛选子因子列表成功
##### 分别请求 GET /api/v1/sub-factors?page=1&limit=5&status=1、status=2、status=3
###### 返回子因子详情状态分别符合新挖库、有效库、失效库筛选条件
#### SF-03 无效 token 查询子因子列表失败
##### 使用伪造 Authorization 请求 GET /api/v1/sub-factors
###### 返回 401 或 403
#### SF-04 创建子因子成功
##### 使用 auto sub_factor_name、cn_name、真实 factor_id 请求 POST /api/v1/sub-factors
###### 返回 success=true，data.id 不为空，创建出的子因子登记清理
#### SF-05 缺少 sub_factor_name 创建子因子失败
##### 不传 sub_factor_name 请求 POST /api/v1/sub-factors
###### 返回 400、401、403、409 或 422，不返回 500
#### SF-06 重复 sub_factor_name 创建子因子失败
##### 连续两次使用相同 sub_factor_name 请求 POST /api/v1/sub-factors
###### 第一次创建成功，第二次返回 400、409 或 422
#### SF-07 查询子因子详情成功
##### 从子因子列表派生 sub_factor_id，请求 GET /api/v1/sub-factors/{sub_factor_id}
###### 返回 success=true，data 为子因子详情
#### SF-08 查询不存在子因子失败
##### 使用 sub_factor_id=999999999 请求 GET /api/v1/sub-factors/{sub_factor_id}
###### 返回 400、404 或 422
#### SF-09 更新子因子成功
##### 先创建 auto 子因子，再请求 PUT /api/v1/sub-factors/{sub_factor_id} 更新 cn_name
###### 更新接口返回 success=true
#### SF-10 更新不存在子因子失败
##### 使用 sub_factor_id=999999999 请求 PUT /api/v1/sub-factors/{sub_factor_id}
###### 返回 400、404 或 422
#### SF-11 更新子因子状态成功
##### 先创建 auto 子因子，再请求 PUT /api/v1/sub-factors/{sub_factor_id}/status 将状态更新为 3
###### 状态更新接口返回 success=true，data.status 或 data.sub_factor_detail.status 等于 3
#### SF-12 批量更新子因子状态成功
##### 先创建 auto 子因子，再请求 PUT /api/v1/sub-factors/status/batch 批量更新状态
###### 批量状态更新接口返回 success=true，data.status 等于 3，updated_sub_factor_ids 包含被更新子因子
#### SF-13 创建子因子刷新任务返回明确结果
##### 从子因子列表派生 sub_factor_id，请求 POST /api/v1/sub-factors/{sub_factor_id}/refresh
###### 返回成功、Accepted 或明确业务错误，HTTP 状态码小于 500
#### SF-14 查询子因子刷新任务状态成功
##### 先触发 refresh 获取 refresh_id，再请求 GET /api/v1/sub-factors/{sub_factor_id}/refresh/{refresh_id}
###### 如果 refresh 返回查询凭证，则状态查询接口返回 success=true
#### SF-15 子因子汇总查询成功
##### 使用有效 token 请求 GET /api/v1/sub-factors/summary?type=new&page=1&limit=5
###### 返回 success=true，data 结构完整
#### SF-16 子因子图表汇总查询成功
##### 使用有效 token 请求 GET /api/v1/sub-factors/graph?type=new
###### 返回 success=true，data 结构完整
#### SF-17 子因子筛选项查询成功
##### 使用有效 token 请求 GET /api/v1/sub-factors/filter-options
###### 返回 success=true，data 结构完整
#### SF-18 复制子因子成功
##### 先创建 auto 子因子，再请求 POST /api/v1/sub-factors/copy 复制该 sub_factor_id
###### copy 接口返回 success=true，复制副本的 sub_factor_detail.status 等于 1，副本保留后由人工或定时任务清理
### 元数据
#### FM-01 Agent Factory 配置查询成功
##### 使用有效 token 请求 GET /api/v1/agent-factory-config?coin_category=main
###### 返回 success=true，data 结构完整
#### FM-02 因子评价标准查询成功
##### 使用有效 token 请求 GET /api/v1/factor-evaluation-standards?coin_category=main
###### 返回 success=true，data 结构完整
#### FM-03 币种池交易对查询成功
##### 使用有效 token 请求币种池交易对接口
###### 返回 success=true，data 结构完整
#### FM-04 因子通知缺少 run_id 失败
##### 使用空 run_id 请求 POST /api/v1/factors/notification
###### 返回 400 或 422，不返回无鉴权误报
#### FM-05 因子通知有效载荷正向场景暂不执行
##### 从 DB 只读查询 selected run_id，并请求 POST /api/v1/factors/notification
###### 配置 webhook secret 且存在可用 run_id 时返回 success=true，data.run_id 与请求一致
### 连贯场景
#### FS-01 主题创建-列表-详情-更新-状态链路
##### 创建 auto 主题后依次查询列表、详情、更新 cn_name、更新状态
###### 链路内每个接口返回成功，列表中能找到创建的 theme_key，最终主题状态等于 3
#### FS-02 因子创建-列表-详情-更新-状态链路
##### 创建 auto 因子后依次查询列表、详情、更新 cn_name、更新状态
###### 链路内每个接口返回成功，最终因子详情状态等于 3
#### FS-03 子因子创建-列表-详情-更新-状态-refresh 链路
##### 创建 auto 子因子后依次查询列表、详情、更新 cn_name、更新状态并触发 refresh
###### 核心接口返回成功，最终子因子详情状态等于 3，refresh 返回非 500 的明确结果
## FactorIC
### 因子 IC 查询
#### IC-01 因子 IC summary 查询成功
##### 从已有母因子 IC 汇总指标派生 factor_id，请求 GET /api/v1/factor-ic/factors/{factor_id}/summary
###### 返回 HTTP 200，success=true，data 结构完整
#### IC-02 不存在因子 IC summary 返回明确结果
##### 使用 factor_id=999999999 请求 GET /api/v1/factor-ic/factors/{factor_id}/summary
###### 返回成功空数据或 400、404、422，不返回 500
#### IC-03 因子 by-symbol 查询成功
##### 从因子列表派生 factor_id，请求 GET /api/v1/factor-ic/factors/{factor_id}/by-symbol
###### 返回 HTTP 200，success=true，data 结构完整
#### IC-04 因子 slice-metrics 查询成功
##### 从因子列表派生 factor_id，请求 GET /api/v1/factor-ic/factors/{factor_id}/slice-metrics
###### 返回 HTTP 200，success=true，data 结构完整
#### IC-05 因子 symbol-window-metrics 查询成功
##### 从因子列表派生 factor_id，请求 GET /api/v1/factor-ic/factors/{factor_id}/symbol-window-metrics?universe_key=main&limit=5
###### 返回 HTTP 200，success=true，data 结构完整
### 子因子 IC 查询
#### IC-06 子因子 summary 查询成功
##### 从已有子因子 IC 汇总指标派生 sub_factor_id，请求 GET /api/v1/factor-ic/sub-factors/{sub_factor_id}/summary
###### 返回 HTTP 200，success=true，data 结构完整
#### IC-07 子因子 by-symbol 查询成功
##### 从子因子列表派生 sub_factor_id，请求 GET /api/v1/factor-ic/sub-factors/{sub_factor_id}/by-symbol
###### 返回 HTTP 200，success=true，data 结构完整
#### IC-08 子因子 slice-metrics 查询成功
##### 从子因子列表派生 sub_factor_id，请求 GET /api/v1/factor-ic/sub-factors/{sub_factor_id}/slice-metrics
###### 返回 HTTP 200，success=true，data 结构完整
#### IC-09 子因子 symbol-window-metrics 查询成功
##### 从子因子列表派生 sub_factor_id，请求 GET /api/v1/factor-ic/sub-factors/{sub_factor_id}/symbol-window-metrics?universe_key=main&limit=5
###### 返回 HTTP 200，success=true，data 结构完整
### 汇总指标
#### IC-10 IC 汇总指标列表查询成功
##### 使用有效 token 请求 GET /api/v1/factor-ic/summary-metrics?limit=5
###### 返回 HTTP 200，success=true，data 结构完整
#### IC-11 批量 upsert IC 汇总指标返回明确结果
##### 先创建 auto 因子，再请求 POST /api/v1/factor-ic/summary-metrics/batch 写入 summary metrics
###### upsert 返回成功，随后按 factor_id 和 run_id 查询能找到本次写入指标
### 切片指标
#### IC-12 IC 切片指标列表查询成功
##### 使用有效 token 请求 GET /api/v1/factor-ic/slice-metrics?limit=5
###### 返回 HTTP 200，success=true，data 结构完整
#### IC-13 批量 upsert IC 切片指标返回明确结果
##### 先创建 auto 因子，再请求 POST /api/v1/factor-ic/slice-metrics/batch 写入 slice metrics
###### upsert 返回成功，随后按 factor_id、run_id 和 symbol 查询能找到本次写入指标
### IC 运行记录
#### IC-14 IC 运行记录列表查询成功
##### 使用有效 token 请求 GET /api/v1/factor-ic/runs?limit=5
###### 返回 HTTP 200，success=true，data 结构完整
#### IC-15 创建 IC 运行记录成功
##### 先创建 auto 因子，再请求 POST /api/v1/factor-ic/runs 创建 IC run
###### 创建接口返回成功，使用返回 run_id 查询详情也返回成功
#### IC-16 查询 IC 运行记录详情成功
##### 从 IC runs 列表派生 run_id，请求 GET /api/v1/factor-ic/runs/{run_id}
###### 返回 HTTP 200，success=true，data 结构完整
#### IC-17 查询不存在 IC 运行记录失败
##### 使用 run_id=999999999 请求 GET /api/v1/factor-ic/runs/{run_id}
###### 返回 400、404 或 422
### 评分标准
#### IC-18 IC 评分标准查询成功
##### 使用有效 token 请求 GET /api/v1/factor-ic/scoring-standards?coin_category=main
###### 返回 HTTP 200，success=true，data 结构完整
### 鉴权
#### IC-19 未带 token 查询 IC summary 失败
##### 不带 Authorization 请求 GET /api/v1/factor-ic/factors/{factor_id}/summary
###### 返回 401 或 403
#### IC-20 无效 token 查询 IC summary 失败
##### 使用伪造 Authorization 请求 GET /api/v1/factor-ic/factors/{factor_id}/summary
###### 返回 401 或 403
### 连贯场景
#### ICS-01 创建 IC run 后查询详情
##### 创建 auto 因子后请求 createRun，再使用返回 run_id 查询 run 详情
###### 创建和详情查询接口都返回成功
#### ICS-02 upsert summary metrics 后查询 summary metrics
##### 创建 auto 因子后 upsert summary metrics，再按 factor_id 查询 summary metrics
###### 查询结果中包含本次写入的 factor_id、run_id 和 mean_ic 指标
#### ICS-03 upsert slice metrics 后查询 slice metrics
##### 创建 auto 因子后 upsert slice metrics，再按 factor_id 和 symbol 查询 slice metrics
###### 查询结果中包含本次写入的 factor_id、run_id、symbol 和 ic 指标
## Admin
### 配置
#### ADC-01 更新 Agent Factory 配置成功
##### 使用管理员 token 请求 PUT /api/v1/admin/agent-factory-config 更新配置
###### 返回 success=true，配置更新接口响应成功
#### ADC-02 非法 Agent Factory 配置更新失败
##### 使用非法 agent_enabled 请求 PUT /api/v1/admin/agent-factory-config
###### 返回 400、401、403 或 422，不返回 500
#### ADC-03 创建因子评价标准暂不执行
##### 使用 auto coin_category 请求 POST /api/v1/admin/factor-evaluation-standards，再更新并登记删除
###### 创建和更新接口返回成功，用例结束后通过删除接口清理
#### ADC-05 更新不存在因子评价标准失败
##### 使用 id=999999999 请求 PUT /api/v1/admin/factor-evaluation-standards/{id}
###### 返回 400、404 或 422
#### ADC-07 删除不存在因子评价标准失败
##### 使用 id=999999999 请求 DELETE /api/v1/admin/factor-evaluation-standards/{id}
###### 返回 400、404 或 422
#### ADC-09 Agent Factory 公共配置查询成功
##### 使用管理员 token 请求 GET /api/v1/agent-factory-config?coin_category=main
###### 返回 success=true，data 结构完整
### 用户
#### ADU-01 用户列表查询成功
##### 使用管理员 token 请求 GET /api/v1/admin/users
###### 返回 HTTP 200，success=true，data 结构完整
#### ADU-02 按状态查询用户列表成功
##### 使用管理员 token 请求 GET /api/v1/admin/users?status=active
###### 返回 HTTP 200，success=true，data 结构完整
#### ADU-03 未带 token 查询用户列表失败
##### 不带 Authorization 请求 GET /api/v1/admin/users
###### 返回 401 或 403
#### ADU-05 创建管理员成功
##### 使用 auto email、display_name 和固定强密码请求 POST /api/v1/admin/admins
###### 返回 success=true，新用户 id 可定位并登记清理
#### ADU-06 重复管理员邮箱创建失败
##### 连续两次使用相同 email 请求 POST /api/v1/admin/admins
###### 第二次返回 400、409 或 422
#### ADU-07 更新自动化用户成功
##### 先创建管理员，再请求 PATCH /api/v1/admin/users/{user_id} 更新 notes
###### 更新接口返回 success=true
#### ADU-08 更新不存在用户失败
##### 使用 user_id=999999999 请求 PATCH /api/v1/admin/users/{user_id}
###### 返回 400、404 或 422
#### ADU-09 删除自动化用户成功
##### 先创建管理员，再请求 DELETE /api/v1/admin/users/{user_id}
###### 删除接口返回 success=true
#### ADU-10 查询用户权限成功
##### 从用户列表派生 user_id，请求 GET /api/v1/admin/users/{user_id}/permissions
###### 返回 HTTP 200，success=true，data 结构完整
#### ADU-11 替换用户权限成功
##### 先创建管理员，再请求 PUT /api/v1/admin/users/{user_id}/permissions 设置 perm_codes=[]
###### 替换权限接口返回 success=true
#### ADU-12 授予用户权限返回明确结果
##### 先创建管理员并从权限列表派生 permission code，再请求 POST /api/v1/admin/users/{user_id}/permissions/{code}
###### 接口成功或返回明确参数错误，不返回 500
#### ADU-13 撤销用户权限返回明确结果
##### 先创建管理员并从权限列表派生 permission code，再请求 DELETE /api/v1/admin/users/{user_id}/permissions/{code}
###### 接口成功或返回明确参数错误，不返回 500
#### ADU-14 解锁不存在用户返回明确结果
##### 使用 auto 不存在邮箱请求 POST /api/v1/admin/users/unlock
###### 返回成功或 400、404、422，不返回 500
#### ADU-15 重置自动化管理员密码成功
##### 先创建管理员，再请求 PATCH /api/v1/admin/admins/{admin_id}/password 设置新密码
###### 重置密码接口返回 success=true
### 角色模板
#### ADR-01 角色模板列表查询成功
##### 使用管理员 token 请求 GET /api/v1/admin/role-templates
###### 返回 HTTP 200，success=true，data 结构完整
#### ADR-02 创建角色模板成功
##### 使用 auto role_name、display_name 请求 POST /api/v1/admin/role-templates
###### 创建接口返回 success=true，角色模板登记清理
#### ADR-03 重复角色模板创建失败
##### 连续两次使用相同 role_name 请求 POST /api/v1/admin/role-templates
###### 第二次返回 400、409 或 422
#### ADR-04 查询角色模板详情成功
##### 先创建角色模板，再请求 GET /api/v1/admin/role-templates/{role_name}
###### 详情接口返回 success=true
#### ADR-05 查询不存在角色模板失败
##### 使用 role_name=auto_missing_role 请求 GET /api/v1/admin/role-templates/{role_name}
###### 返回 400、404 或 422
#### ADR-06 更新角色模板成功
##### 先创建角色模板，再请求 PATCH /api/v1/admin/role-templates/{role_name} 更新 description
###### 更新接口返回 success=true
#### ADR-07 查询角色模板权限显示名成功
##### 先创建空权限角色模板，再请求 GET /api/v1/admin/role-templates/{role_name}/permission-names
###### 返回 HTTP 200，success=true，data 结构完整
#### ADR-08 删除角色模板成功
##### 先创建角色模板，再请求 DELETE /api/v1/admin/role-templates/{role_name}
###### 删除接口返回 success=true
#### ADR-09 删除不存在角色模板失败
##### 使用 role_name=auto_missing_role 请求 DELETE /api/v1/admin/role-templates/{role_name}
###### 返回 400、404 或 422
#### ADR-11 权限定义列表查询成功
##### 使用管理员 token 请求 GET /api/v1/admin/permissions
###### 返回 HTTP 200，success=true，data 结构完整
#### ADR-12 邀请码列表查询成功
##### 使用管理员 token 请求 GET /api/v1/admin/invite-codes
###### 返回 HTTP 200，success=true，data 结构完整
### 提示词
#### ADP-01 提示词列表查询成功
##### 使用管理员 token 请求 GET /api/v1/admin/prompts?limit=5
###### 返回 HTTP 200，success=true，data 结构完整
#### ADP-02 按 used_by 查询提示词成功
##### 使用管理员 token 请求 GET /api/v1/admin/prompts?used_by=api_test&limit=5
###### 返回 HTTP 200，success=true，data 结构完整
#### ADP-03 创建提示词成功
##### 使用 auto name、type=system、used_by=api_test 请求 POST /api/v1/admin/prompts
###### 创建接口返回 success=true，data.id 不为空，提示词保留后由人工或定时任务清理
#### ADP-04 缺少 name 创建提示词失败
##### 不传 name 请求 POST /api/v1/admin/prompts
###### 返回 400、401、403、409 或 422
#### ADP-05 更新提示词成功
##### 先创建 auto 提示词，再请求 PUT /api/v1/admin/prompts/{prompt_id} 更新 user_prompt
###### 更新接口返回 success=true
#### ADP-06 更新不存在提示词失败
##### 使用 prompt_id=999999999 请求 PUT /api/v1/admin/prompts/{prompt_id}
###### 返回 400、404 或 422
### 连贯场景
#### ADS-01 角色模板创建-列表-详情-更新-权限名-删除链路
##### 创建 auto 角色模板后依次查询列表、详情、更新、查询权限名并删除
###### 链路内每个接口都返回成功
#### ADS-02 用户权限创建-替换-查询-删除链路
##### 创建 auto 管理员后替换权限、查询权限并删除用户
###### 链路内每个接口都返回成功
#### ADS-04 提示词创建-列表-更新链路
##### 创建 auto 提示词后按名称查询列表并更新 user_prompt
###### 链路内每个接口都返回成功，提示词保留后由人工或定时任务清理
## Quantitative_Trading
### 量化账户
#### ADQ-01 量化账户列表查询成功
##### 使用管理员 token 请求 GET /api/v1/admin/quant-accounts
###### 返回 HTTP 200，success=true，data 结构完整
#### ADQ-02 创建量化账户成功
##### 使用 auto email、api_key、secret_key 请求 POST /api/v1/admin/quant-accounts
###### 创建接口返回 success=true，data.id 不为空，量化账户登记清理
#### ADQ-03 缺少 api_key 创建量化账户失败
##### 删除 payload 中 api_key 后请求 POST /api/v1/admin/quant-accounts
###### 返回 400、401、403、409 或 422
#### ADQ-04 重复量化账户创建失败
##### 使用相同 exchange、email、api_key、secret_key 连续两次请求 POST /api/v1/admin/quant-accounts
###### 第二次创建返回 400、401、403、409 或 422；如果接口允许重复创建则用例失败并登记清理重复数据
#### ADQ-05 查询量化账户详情成功
##### 先创建量化账户，再请求 GET /api/v1/admin/quant-accounts/{account_id}
###### 详情接口返回 success=true
#### ADQ-06 更新量化账户成功
##### 先创建量化账户，再请求 PATCH /api/v1/admin/quant-accounts/{account_id} 更新 api_description
###### 更新接口返回 success=true
#### ADQ-07 更新量化账户资产成功
##### 先创建量化账户，再请求 PATCH /api/v1/admin/quant-accounts/{account_id}/assets 更新 total_assets_usdt
###### 资产更新接口返回 success=true
#### ADQ-08 查询存储量化账户实时信息
##### 使用 EXCHANGE_TEST 配置创建量化账户，再请求 GET /api/v1/admin/quant-accounts/{account_id}/account-info
###### 返回 HTTP 200，success=true，data 结构完整
#### ADQ-09 直接交易所账户查询成功
##### 使用 EXCHANGE_TEST 配置请求 POST /api/v1/admin/exchange/account
###### 返回 HTTP 200，success=true，data 结构完整
#### ADQ-10 错误交易所 key 查询失败
##### 使用错误 api_key 和 secret_key 请求 POST /api/v1/admin/exchange/account
###### 返回 400、401、403 或 502
#### ADQ-11 查询不存在量化账户失败
##### 使用 account_id=999999999 请求 GET /api/v1/admin/quant-accounts/{account_id}
###### 返回 400、404 或 422
#### ADQ-12 删除量化账户成功
##### 先创建量化账户，再请求 DELETE /api/v1/admin/quant-accounts/{account_id}
###### 删除接口返回 success=true
### 连贯场景
#### QT-01 量化账户创建-列表-详情-更新-资产-删除链路
##### 创建 auto 量化账户后依次查询列表、详情、更新描述、更新资产并删除
###### 链路内每个接口都返回成功
