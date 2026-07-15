# API 契约

后端 FastAPI，标准业务接口挂在前缀 **`/api/v1`** 下（`backend/app/main.py` → `api/v1/router.py`）；内置执行器兼容入口可挂在独立前缀，例如 AI Hybrid 的 `/aihybrid/api/submissions`。

## 命名约定（重要）

- **后端 schema 全部 snake_case**（`backend/app/schemas/`，纯 Pydantic，无 alias）。
- **前端全部 camelCase**：`web/src/api/client.ts` 的 `request()` 出站自动 `toSnake(body)`、入站自动 `toCamel(data)`。
- 因此：本文档接口体用「概念字段」描述；前端写 camelCase（`caseIds`），后端收 snake（`case_ids`），无需手动转。
- 错误返回 `{"detail": ...}`，前端 `ApiError` 携带 `status` 和 `detail`。

## 路由清单（`api/v1/routes/`）

### 健康
- `GET /api/v1/healthz` — 健康检查。
- `GET /api/v1/config` — 返回前端可读取的安全配置。当前含 `{ os_agent_enabled }`；前端据此决定是否展示 OS Agent。

### 用户 / 需求体系（`routes/workbench.py`）
- `GET /api/v1/users` — 用户列表（仅飞书同步的测试人员）。
- `GET /api/v1/requirements` — 一级目录（含其二级需求 items，item 带 `version`）。**一级目录按创建时间倒序**。
- `GET /api/v1/requirement-catalog` — 需求目录。支持 `page/page_size/source_space/person_id/sprint_id/testing_only/keyword/focus_item_id` 服务端筛选与分页；返回 `{ groups, ungroupedItems, total, page, pageSize, filterUserIds, sprints }`，其中 `ungroupedItems` 是未进入一级目录的二级需求。`page_size=0` 表示兼容旧行为返回全部。
- `GET /api/v1/requirement-pool` — 外部项目池（飞书来源技术缓存）。支持 `page/page_size/source_space/person_id/sprint_id/testing_only/keyword/bound_status` 服务端筛选与分页；返回 `{ items, total, attachableTotal, page, pageSize, filterUserIds, sprints }`。飞书项目业务上等于二级需求，每条含 `boundItemId/boundGroupId`、`sourceSpace`、`ownerUserId`、`ownerName`，以及展示卡片 `card`：`{ number(工作项id), status(当前节点名), createdDate, link(飞书直达), roles:[{label:测试/前端/后端, names[]}], sprints:[{id,name}] }`。**按工作项创建时间倒序**。
- `POST /api/v1/requirement-groups/create-with-pool` — 新建一级目录并纳入项目。体：`{ name, items:[{poolId, version}] }`。**version 必填且组内唯一**；如果该飞书项目已有未进入目录的二级需求，则直接绑定该 item，不重复创建。
- `POST /api/v1/requirement-groups/{group_id}/add-pool` — 纳入已有目录。体：`{ items:[{poolId, version}] }`。version 组内唯一；如果该飞书项目已有未进入目录 item，则绑定同一个 item，不重复创建。
- `POST /api/v1/requirement-items/create-from-pool` — 内部兜底接口。体：`{ poolIds:[number] }`；返回 `{ message, items }`。正常业务路径由飞书 pull 自动生成 item；开发期旧脏数据不做产品兼容，直接删库重拉。
- `POST /api/v1/requirement-groups/{group_id}/bind-items` — 把已有未归属二级需求纳入目录。体：`{ items:[{requirementItemId, version}] }`；version 必填且组内唯一；返回 `{ message, group }`。
- `POST /api/v1/requirement-items/{item_id}/unbind-group` — 把二级需求移出一级目录，保留 case/执行记录/报告并清空 version；返回 `{ message, item }`。
- `PATCH /api/v1/requirement-items/{item_id}/version` — 修改某已归属二级需求版本。体：`{ version }`；组内唯一校验；返回 `{ message, group }`。
- `PATCH /api/v1/requirement-items/{item_id}/auto-discovery` — 开/关该二级需求的自动发现（检查点 4）。体：`{ enabled }`；返回 `{ requirementItemId, autoDiscoveryEnabled }`。[当前] 仅持久化开关意图，尚未接入执行/发现。

### 飞书项目来源（`routes/workbench.py` → `services/sources/feishu_project.py`）
- `GET /api/v1/sources/feishu-project/spaces` — 配置里可拉取的空间列表 `{ spaces:[{projectKey, name}] }`（供前端空间筛选）。
- `POST /api/v1/sources/feishu-project/pull?project_keys=a&project_keys=b` — 启动后台拉取任务并立即返回任务快照 `{ jobId, status, message, fetched, created, updated, spaces[] }`。不传 `project_keys` 则拉配置里全部 spaces。只拉「QA(测试人员)已绑定」的需求；每条入池项目会同步确保存在 1:1 二级需求，默认 `groupId = null`（未进入目录）。
- `GET /api/v1/sources/feishu-project/pull-jobs/{job_id}` — 查询后台拉取任务状态。成功后返回累计 `{ fetched, created, updated, spaces:[{projectKey,name,fetched,matched,created,updated,skipped_no_qa,error?}] }`。凭证走 env，空间/角色/状态/迭代映射走 `backend/config/feishu_project.json`（详见 `飞书项目接入.md`）。

### Function Map 资产与挂载（`routes/function_map_assets.py`）

> [当前] Function Map 管理采用「资产库 + 挂载」模型；飞书目录页的旧 functionMap 文件入口已下线，新前端不再调用旧文件接口。批次执行、提 bug、修复和助手对话都读取新挂载编译的顶层上下文（`compile_top_level_context`：适用端过滤、去重、带资产边界、无全局兜底），不再读取旧 `requirement_groups.function_map_files`。旧的 `/requirement-groups/{group_id}/function-map` 文件接口后端仍保留、已无生产读者，可择机退役。详见 [Function Map](Function-Map.md) 和 [执行上下文协议](function%20map%20skills化执行单元协议.md)。

资产（管理，标准 camelCase 出入站）：
- `GET /api/v1/function-map-assets?target=&keyword=&page=&page_size=` — 分页列出资产摘要（**不含正文**）。返回 `{ items:[{id, title, description, targets, updatedAt, referenceCount}], total, page, pageSize }`。
- `POST /api/v1/function-map-assets` — 新建资产。体：`{ title, description, content, targets[], sourceFilename? }`；正文可直接填写，带 `sourceFilename` 时表示本地文本导入；**标题全局唯一**，重名 400。返回资产详情。
- `GET /api/v1/function-map-assets/{id}` — 资产详情，含正文 `content`、挂载引用 `mounts:[{scope,id,name}]` 与 `referenceCount`。
- `PATCH /api/v1/function-map-assets/{id}` — 编辑元信息 `{ title, description, targets[] }`，正文不变；重名 400、不存在 404。
- `PUT /api/v1/function-map-assets/{id}/content` — 保存正文。体：`{ content, sourceFilename? }`；不带 `sourceFilename` 为直接填写，带文件名为本地文本导入覆盖，均不改标题、适用场景、适用端。
- `GET /api/v1/function-map-assets/{id}/export` — 导出 `{ title, description, content, targets }`（前端存为标题命名的 `.md`）。
- `DELETE /api/v1/function-map-assets/{id}` — 删除资产（挂载关系随 FK `ON DELETE CASCADE` 清理；前端按 `referenceCount` 提示引用）。

挂载目标 / 挂载关系：
- `GET /api/v1/function-map-mount-targets?keyword=&page=&page_size=&focus_group_id=&focus_item_id=` — 顶层分页的挂载目标：全部一级目录（含空目录，组内二级需求全带）+ 未进入目录的二级需求同级。传 `focus_*` 时忽略 `page`、返回目标所在页（深链定位）。返回 `{ groups:[{id, name, items[]}], ungroupedItems[], total, page, pageSize }`。
- `GET|POST|DELETE /api/v1/requirement-groups/{group_id}/function-map-mounts[/{asset_id}]` — 一级目录的挂载列表 / 挂载 / 移除。POST 体 `{ assetId }`；返回该目录挂载列表（资产摘要）。
- `GET|POST|DELETE /api/v1/requirement-items/{requirement_item_id}/function-map-mounts[/{asset_id}]` — 二级需求同上。
- `GET|POST|DELETE /api/v1/quick-sessions/{quick_session_id}/function-map-mounts[/{asset_id}]` — 快速会话从资产库选中的 Function Map（选/取消/列表）。POST 体 `{ assetId }`；返回该会话选中列表（资产摘要）。快速执行/提 bug/修复/助手 quick 按这些编译上下文。

执行流水（调用台账，检查点 7；`routes/execution_logs.py`）：
- `GET /api/v1/execution-strategy-call-logs?status=&executor=&mode=&page=&page_size=` — 只读列出执行策略调用日志（`execution_strategy_call_logs`），按 `created_at` 倒序分页（`page_size` 上限 100）。返回 `{ items[], total, page, pageSize }`；每条含触发人（`triggerUserId`/`triggerUserName`）、关联标题（`requirementItemTitle`/`quickSessionTitle`，供 UI 显示人可读名而非裸 id）、模式/范围/入口/执行器、`caseIds`、`submissionId`、`status`、`failureReason`、输入摘要 `input`，以及 **`submittedFunctionMapContext`（从该次批次 `raw_request` 读出的、端过滤后本次实际提交的 functionMapContext，用于直接核对 map 有没有带入、带了哪些）**，另有检查点 6 才填充的 `functionMapResult`/`effectiveContext`。前端「执行流水」Tab 消费。

旧文件接口（[当前·旧] `workbench.py`，后端仍在、新前端不再使用，待清理）：
- `GET /api/v1/requirement-groups/{group_id}/function-map` — 列出文件。返回 `{ groupId, files:[{filename, content, charCount}], totalChars, maxChars }`。
- `POST /api/v1/requirement-groups/{group_id}/function-map` — 上传/覆盖一个文件。体：`{ filename, content }`；同名覆盖；合并后超 `maxChars`（默认 8000）返回 400。
- `DELETE /api/v1/requirement-groups/{group_id}/function-map?filename=...` — 删除一个文件。

### 首页工作台
- `GET /api/v1/home?user_id=<id>` — 首页看板（概览/任务/用户）。`requirements[]` 每个二级需求含 `autoDiscoveryEnabled`（自动发现开关状态，检查点 4）。
- `GET /api/v1/workbench-cases?requirement_item_id=<id>` — 某二级需求的 case 列表（执行视角），每条 case 返回 `batchId` 作为所属测试集真实身份。
- `GET /api/v1/cases/{case_id}` — 单个 case 详情。

### Case 资产维护
- `POST /api/v1/cases` — 在已有测试集内新增单条用例。体：`{ requirementItemId, batchId, pathNodes[], rawTitle, preconditions, stepsText, expectedResult }`；`batchId` 必须属于该二级需求，`pathNodes` 必须是该测试集里已存在的一条完整层级路径。新增后不重新判定执行端，默认执行端为 `manual`，复用现有“变更待确认”语义；标题开头的 `【标签】` 只复制识别为元数据，不从完整标题中删除。旧 case 的顺序不重排。核心字段只传正文，后端按同层级既有 case 的 raw 节点模板补 `测试标题：/前置条件：/操作步骤：/预期结果：` 前缀。
- `PATCH /api/v1/cases/{case_id}` — 编辑用例（标题使用完整 `rawTitle`，另含前置/步骤/预期，资产模式可改层级）。只提交旧 `cleanTitle` 修改标题会明确拒绝；测试标题、前置条件、操作步骤、预期结果按 Markdown 核心字段契约保存为单行文本，字段内部真实换行会折叠为空格。
- `DELETE /api/v1/cases/{case_id}` — 删除用例。
- `POST /api/v1/case-suites/export?requirement_item_id=<id>&batch_id=<id>` — 按测试集批次导出当前 Markdown。返回 `{ batchId, suiteTitle, filename, content, caseCount }`；只导出测试集、路径层级、测试标题、前置条件、操作步骤、预期结果，不导出执行状态、报告、bug、functionMap 或人员信息。导出的每个 Markdown 节点都是单行列表项，可被标准 Markdown 导入再次解析。
- `DELETE /api/v1/case-suites?requirement_item_id=<id>&batch_id=<id>` — 按测试集批次删除该测试集及其全部 case。返回 `{ requirementItemId, batchId, suiteTitle, deletedCaseCount, deletedRunningCount, deletedBatchId, message }`；不删除二级需求、一级目录或 functionMap。若外部执行回调晚到，按已删除资产忽略。
- `POST /api/v1/case-work-items/update` — 改执行态（状态/执行器）。体含 `caseId` + 要改的字段。
- `POST /api/v1/case-work-items/coverage` — 改覆盖标记（端/浏览器泳道三态）。体 `{caseId, lane, state}`，`lane∈{android,ios,harmony,chrome,safari,firefox}`、`state∈{none,passed,failed}`；只改 `coverage`，不碰执行态/报告/bug。返回 `{caseId, coverage}`。详见 `端覆盖标记方案.md`。

### Markdown 导入与打磨碰撞
- `POST /api/v1/imports/markdown` — 上传解析；同一二级需求下仅当来源文件名相同才可能返回 `mode: collision_review`（进碰撞审批），文件名不同则作为独立测试集确认；Markdown 一级标题只作为测试集展示标题。层级由部署级公开配置决定，见 [Markdown 导入与层级配置](Markdown层级配置.md)。导入时核心字段内的非列表续行会合并到上一条字段文本，并折叠为空格。
- `POST /api/v1/imports/markdown/commit` — 提交碰撞审批决策落库（整体提交）。请求体除 `requirementItemId`、`filename`、`content`、`decisions` 外必须带展示阶段返回的 `reviewId`。后端复用该快照，不重算碰撞或再次调用碰撞模型；若快照过期、文件/来源不一致，或审批期间当前测试集已变化，明确返回“重新导入碰撞”，不会按旧结果写入。提交时沿用同一 Markdown 核心字段单行化规则。

### 快速模式（`routes/quick.py`）
- `POST /api/v1/quick/sessions/import` — 导入 Markdown 创建 quick session。体：`{ filename, content, functionFiles[] }`（`functionFiles` 为 [当前·旧·停用] 字段，快速模式 Function Map 已改走 `function_map_quick_mounts` 挂载，导入不再需要传）。结构错误整体 422，不半导入；核心字段内的非列表续行会合并到上一条字段文本，并折叠为空格。导入时复用标准执行端打标：规则优先；Web 规则包含平台、后台、管理端、控制台、站点/网站、cb、vm、视频后台、课程后台，不包含单独的“课程”；规则未命中时同步模型判断，模型失败或无结果时按人工执行兜底。
- `GET /api/v1/quick/sessions/{session_id}` — 读取 quick session 与 case 列表，用于刷新恢复/接力。
- `PATCH /api/v1/quick/sessions/{session_id}` — 更新 session 级信息：`suiteTitle?`、`feishuRequirementUrl?`、`currentUserId?`；`functionFiles?` 为 [当前·旧·停用] 字段（Function Map 改走 `/quick-sessions/{id}/function-map-mounts`，新前端不再写）；`feishuBugUrl?` 仅为旧 session 兼容字段，新流程不再使用。
- `POST /api/v1/quick/sessions/{session_id}/feishu-target` — 绑定飞书需求链接并实时读取目标工作项，同时校验该空间是否已在标准版完成需求配置和 bug 模板配置。提 bug 前必需。
- `POST /api/v1/quick/sessions/{session_id}/feishu-link-check` — 校验已保存的飞书需求链接。体 `{ url, kind }`，新流程只使用 `kind=requirement`；解析成功后缓存目标快照，解析失败返回 `readable=false/message`。
- `POST /api/v1/quick/sessions/{session_id}/export?clear=false|true` — 导出编辑后的 Markdown；`clear=false` 只导出并保留 session，`clear=true` 导出成功后清理该 quick session（默认 `true`，兼容旧调用）。导出的每个 Markdown 节点都是单行列表项，可被快速模式 Markdown 导入再次解析。
- `DELETE /api/v1/quick/sessions/{session_id}` — 退出 quick session，清理全部 quick 数据。
- `PATCH /api/v1/quick/cases/{case_id}` — 编辑当前导入 case 的标题、前置、步骤、预期；不支持编辑测试集标题、路径层级，也不支持新增/删除/排序。核心字段内部真实换行会折叠为空格。
- `POST /api/v1/quick/case-work-items/update` / `coverage` — quick 专属执行态和覆盖标记更新。
- `GET /api/v1/quick/aiphone/devices` — quick 页面读取 AI Phone 设备。
- `GET /api/v1/quick/aiweb/devices` — quick 页面读取 AI Web 浏览器槽。
- `POST /api/v1/quick/executions/aiphone/submit` — quick session 内提交 AI Phone。体含 `sessionId`、`caseIds[]`、设备池、cache/retry、可选 `executionRequestGroupId`。Function Map 走快速会话从资产库选中的挂载编译（`compile_quick_context`）。[当前] 检查点 7 已后台化，返回体新增 `callId`。
- `POST /api/v1/quick/executions/aiweb/submit` — quick session 内提交 AI Web。体含 `sessionId`、`caseIds[]`、`deviceAliasPools{浏览器:[槽别名]}`、`submissionName`、可选 `executionRequestGroupId`；浏览器支持 `chrome/safari/firefox`，AI Web 的 `webkit` 在 case-flow 内映射为 `safari`；Function Map 同上走 quick 挂载编译。后台化，返回 `callId`。
- `POST /api/v1/quick/executions/aiapi/submit` — quick session 内提交 AI API。体含 `sessionId`、`caseIds[]`、`submissionName`、可选 `executionRequestGroupId`；不需要设备池，不对外 callback；Function Map 走 quick 挂载编译。AI API 本就后台执行，补记调用日志、返回 `callId`。
- `POST /api/v1/quick/aiphone/callback/{callback_token}` — quick AI Phone 回调；session 已清理后的孤儿回调返回 no-op，不影响标准回调。
- `POST /api/v1/quick/aiweb/callback/{callback_token}` — quick AI Web 回调；提交时按 `CASE_FLOW_AIWEB_CALLBACK_BASE_URL` 拼出，session 已清理后的孤儿回调返回 no-op。
- `POST /api/v1/quick/cases/repair-preview` / `repair-drafts/{draft_id}/apply` — quick 专属诊断修复草稿与应用。
- `GET /api/v1/quick/cases/{case_id}/bug-draft?user_id=` — quick bug 草稿。`user_id` 必填；需求链接必须已绑定，且所属空间必须命中标准版完整配置。
- `POST /api/v1/quick/cases/{case_id}/bug?user_id=` — 从 quick session 提交 bug 到飞书项目。只写 quick 记录和外部飞书 bug，不写正式需求池/正式 case 链；截图在后台通过 MCP 第二通道渲染进描述，失败只写后台日志、不退回链接。

### OS Agent 对话模式（`routes/agent.py`）
- 受 `CASE_FLOW_OS_AGENT_ENABLED` 总控影响；关闭时前端不展示入口，后端 `/api/v1/agent/*` 返回 `404 OS Agent 未启用`。
- `GET /api/v1/agent/session?user_id=` — 读取或创建当前用户绑定的单会话。返回 `{ session, messages[] }`；一个用户最多一条 `agent_sessions`。
- `DELETE /api/v1/agent/session?user_id=` — 清空当前用户的 OS Agent 上下文并重新开始。会删除该会话下的消息、工具调用记录、自然语言 bug 快照和临时状态，然后写入一条新的能力引导消息。
- `POST /api/v1/agent/messages?user_id=` — 向助手发送自然语言消息。体：`{ content, attachments, context_ref? }`；`attachments.images[]` 可携带已上传图片。`context_ref` 只传当前工作台引用和开关，不传 functionMap 正文，支持 `{ mode:"standard", requirement_item_id, use_current_function_map }` 与 `{ mode:"quick", quick_session_id, use_current_function_map }`。后端根据模型工具调用或兜底规则选择 AI Phone / AI Web / AI API / 自然语言提 bug。CLI 只可能是部署方自行开发并注册的一种可选 Tool；开源版本不内置公司的 CLI Tool，业务数据准备请求会明确提示需要二次开发。
- 助手携带 functionMap 时，模型决策阶段不读取正文；工具决策后由后端按 `context_ref` 实时读取并注入下游。标准模式走「当前二级需求 + 其所属一级目录的显式挂载资产」编译（`compile_top_level_context`，去重、带资产边界、无 8000 上限），quick 模式走「快速会话从资产库选中的资产」编译（`compile_quick_context`，读 `function_map_quick_mounts`）；取不到时不注入，也不回落全局 env。旧 `quick_sessions.function_files` 已停用不再读。
- `POST /api/v1/agent/uploads?user_id=` — 上传助手消息图片，`multipart/form-data` 字段名 `files`。返回 `{ images:[{url, thumbnail_url, filename, mime, size}] }`；图片通过 `/media/agent_uploads/` 暴露。
- `GET /api/v1/agent/tools` — 返回助手当前注册工具清单，用于调试和前端能力提示。
- `POST /api/v1/agent/aiphone/callback/{callback_token}` — 助手提交给 AI Phone 后的回调入口，只写 `agent_dispatches/agent_messages`。
- `POST /api/v1/agent/aiweb/callback/{callback_token}` — 助手提交给 AI Web 后的回调入口，提交时按 `CASE_FLOW_AIWEB_CALLBACK_BASE_URL` 拼出，只写 `agent_dispatches/agent_messages`。
- 助手模式不写正式 `case_work_items`，也不写 quick 表；提 bug 是自然语言提交，缺需求链接等硬前置时在对话里追问，不弹字段级确认表单。

### 错误修复
- `POST /api/v1/cases/repair-preview` — 读报告生成诊断/修复候选草稿。体：`{ caseIds[] }`。失败有报告的 case 也会被后台自动触发诊断。
- `POST /api/v1/cases/repair-drafts/{draft_id}/apply` — 采用某条修复草稿（可改操作步骤/前置/预期，重置执行态）。体：`{ stepsText?, preconditions?, expectedResult? }`。写回核心字段时按 Markdown 单行化契约折叠真实换行。
- `GET /api/v1/cases/{case_id}/bug-draft?user_id=` — 取提交 bug 的预填草稿（复刻飞书建单表单：字段/顺序/必填/选项/默认 + 我们的映射值）。优先用后台预生成草稿，秒开。
- `POST /api/v1/cases/{case_id}/bug?user_id=` — 提交 bug 到飞书项目。体：`{ title, description, fields[], key_images? }`（fields 为弹窗回传的字段，含编辑后的选择）。同步建单返回链接，单选/多选后台异步回写，截图通过 MCP 第二通道渲染进描述；仅失败 case 可提，可对同一 case 多次提交。详见 `提交bug与飞书交互约定.md`。

### AI Phone 执行（详见 `AI-Phone集成.md`）
- `GET /api/v1/aiphone/devices` — 可用设备（空闲∪占用，带 `occupancy`）。
- `POST /api/v1/executions/aiphone/submit` — 提交执行。体：`{ caseIds[], deviceAliasPools{平台:[别名]}|null, submissionName, cacheMode('off'|'v1'|'v2'|'v3'), retryMax }`。
- `POST /api/v1/aiphone/callback/{callback_token}` — AI Phone 回调入口（由 AI Phone 调，按 token 找批次落状态）。

### AI Web 执行（详见 `AI-Web集成.md`）
- `GET /api/v1/aiweb/devices` — 可用浏览器槽（空闲∪占用，带 `occupancy`）。
- `POST /api/v1/executions/aiweb/submit` — 提交 Web case 到 AI Web。体复用 `{ caseIds[], deviceAliasPools{浏览器:[槽别名]}, submissionName, cacheMode, retryMax }`；浏览器支持 `chrome/safari/firefox`，AI Web 的 `webkit` 在 case-flow 内映射为 `safari`。
- `POST /api/v1/aiweb/callback/{callback_token}` — AI Web 回调入口（由 AI Web 调，提交时按 `CASE_FLOW_AIWEB_CALLBACK_BASE_URL` 拼出，按 token 找批次落状态）。

### AI API 执行（详见 `AI-API集成.md`）
- `POST /api/v1/executions/aiapi/submit` — 提交 API case 到内置 AI API。体复用 `{ caseIds[], submissionName }`；`deviceAliasPools/cacheMode/retryMax` 会被忽略。返回体沿用提交结果 DTO，`callbackUrl` 为空字符串。
- `POST /api/v1/quick/executions/aiapi/submit` — quick session 内提交 API case 到内置 AI API，体为 `{ sessionId, caseIds[], submissionName? }`。
- AI API 没有外部 callback；后端创建 `executor="ai_api"`、`platform="api"` 的 batch/item 后，在本进程后台执行模型编译、HTTP 请求、断言和 HTML 报告生成。标准工作流写 `aiphone_execution_*`，quick 写 `quick_execution_*`。
- 标准/quick 的 AI API 目前只有内部停止钩子，不新增 HTTP 取消路由，也不改变现有界面停止按钮的行为。它只会取消本进程协程和后续调用，不推断已经发出的 HTTP 请求是否被目标服务处理。

### AI Hybrid 执行（详见 `Hybrid混合执行器方案.md`）
- `POST /api/v1/executions/aihybrid/submit` — 提交 mixed case 到内置 AI Hybrid。体复用 `{ caseIds[], submissionName }`；不需要设备池，`cacheMode/retryMax` 会被忽略。
- `POST /api/v1/aihybrid/callback/{callback_token}` — AI Hybrid 父回调入口，按标准执行批次写回 `case_work_items`。
- `POST /api/v1/aihybrid/child-callback/{callback_token}` — AI Hybrid 子工具回调入口，只解除编排器内部等待，不写业务 case 状态。
- `POST /aihybrid/api/submissions` — 内置 AI Hybrid 服务入口。它与外部执行器协议同构，接收 `{ submissionName, callbackUrl, functionMapContext?, functionMaps?, items[] }`；`items[]` 内也可带自己的 `functionMapContext`、`functionMaps`。Hybrid 合并顶层与 item 级上下文；结构化 Map 按 `asset_id` 去重、顶层优先。标准/快速执行由后端从显式挂载编译并注入，外部调用可传同形数据。Hybrid 逐份读取结构化 Map 正文并按 `targets` 作为对应端的编排参考，正文仍原样透传给 AI Phone/Web/API。
- `POST /aihybrid/api/submissions/{submission_id}/cancel` — 停止 Hybrid 自身编排。体为可选 `{ caseIds[] }`，省略或空数组表示该 submission 的全部 case；响应 `{ submissionId, acceptedCaseIds[] }` 只确认 Hybrid 已接受本地停止。它不会向已提交的 AI Phone/Web 子任务发取消、查询或等待请求；迟到子回调会被忽略，停止单元不产出报告或 item 完成/失败回调。只停部分 case 时，其余 case 仍可正常收尾并发送父终态。详见 [AI Hybrid 对外接口](AI-Hybrid对外接口.md)。
- AI Hybrid 写 `executor="ai_hybrid"`、`platform="mixed"`；`needs_human` 映射为失败原因而不是新的执行状态。失败有报告时**会触发自动诊断修复**（总报告内嵌结构化证据 + 各失败端错误截图，`read_report(executor="ai_hybrid")` 无损解析，诊断/提 bug 逐端带图）。

### AI API 对外 Direct Run（详见 `AI-API对外接口.md`）
- `POST /api/v1/aiapi/run` — 外部系统直接调用 AI API 能力，不需要创建正式 case 或 quick session。外部直接 HTTP 调用使用 snake_case，体含 `{ title, preconditions, steps_text, expected_result, function_map_context, submission_name?, return_report_html? }`；后端执行模型编译、HTTP 请求、断言和 HTML 报告生成，响应返回 `{ run_id, status, status_reason, report_url, report_html?, result }`。
- Direct Run 不写业务执行状态、不创建执行批次；它只返回本次调用结果和报告。执行范围和请求约束仍走 AI API allowlist / method / timeout 配置，其中 allowlist 为空时不限制 host。

## 约定与边界

- 单条执行与批量执行都走对应执行器 submit 路由；区别只是前端传的 case 数量、是否带 `cacheMode/retryMax`（单条默认 off/0）、以及是否需要设备池。
- 同一次前端执行队列可以混合 App/Web/API/Hybrid/人工；确认后前端按执行器分组推送：App 子集走 AI Phone，Web 子集走 AI Web，API 子集走 AI API，Hybrid 子集走 AI Hybrid，人工暂本地置为执行中。
- [当前] 标准与 Quick 的“停止执行”继续复用 case-work-item 更新接口、本地结果仍是 `not_run`。后端会在删除执行 item 前快照取消目标，提交本地重置后异步发送执行器 cancel hook：AI Phone / AI Web 用 submission + external case + platform，AI Hybrid 用 `{ caseIds[] }`，AI API 调内部停止函数；不在响应中返回或保存取消结果，hook 失败不影响本地停止。
- [当前] 检查点 7：AI Phone/Web/Hybrid 的 submit 已后台化——前台只做入参校验 + 标记执行中 + 建调用日志后秒回（返回体新增 `callId`），编译 Function Map 与提交执行器在进程内后台任务完成。**明显入参错误（case 不存在、端不匹配）仍前台 400**；**编译/提交执行器失败无外部回调，由后端把这些 case 回写 `failed` 并写卡片原因**（不再前台弹窗）。前端一次点击生成 `executionRequestGroupId` 随各路 submit 带上，聚合到 `execution_strategy_call_logs`（迁移 0031）。
- `functionMapContext` 不在请求体里手填：后端在 submit（后台任务）时按执行容器的显式挂载编译后注入（见集成文档）。
- 无鉴权（内网通用）。

## 加新接口时

1. `schemas/` 定义 snake_case DTO。
2. `services/` 写领域逻辑（路由保持轻量）。
3. `routes/workbench.py`（或新 route 模块）挂路由，`router.py` include。
4. 前端用 camelCase 调 `request()`。
5. 更新本文档。
