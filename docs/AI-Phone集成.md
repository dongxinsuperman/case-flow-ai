# AI Phone 集成

<!-- RELATED_REPOSITORY_SLOT: 发布前补入维护者确认的 AI Phone 公开仓库链接。 -->

case-flow 把 App 类（`execution_target=app`）用例分发给 **AI Phone** 执行，靠**回调**接收结果。集成逻辑集中在 `backend/app/services/executions.py`。

Web 类（`execution_target=web`）同构接入 **AI Web**，见 `AI-Web集成.md`。两者复用执行批次表，通过 `aiphone_execution_batches.executor` 区分 `ai_phone` / `ai_web`。

> 现状：AI Phone / AI Web 已按同构协议在 service 层共用提交与回调编排；`executors/` 仍是 Protocol 占位，后续有更多执行器时再继续下沉。

## 配置（env，前缀 `CASE_FLOW_`）

| env | 默认 | 说明 |
|---|---|---|
| `CASE_FLOW_AIPHONE_BASE_URL`（别名 `AI_PHONE_BASE_URL`/`AIPHONE_BASE_URL`） | `http://127.0.0.1:8000` | AI Phone 地址：查设备 + 提交都打这里 |
| `CASE_FLOW_PUBLIC_BASE_URL` | `http://127.0.0.1:8800` | 回调地址前缀，**必须是 AI Phone 能反向访问到的 case-flow 地址** |
| `CASE_FLOW_FUNCTION_MAP_CONTEXT_MAX_CHARS` | `8000` | 旧 functionMap 文件合并字符上限（仅旧文件上传路径用；新资产模型无此上限） |
| `CASE_FLOW_FUNCTION_MAP_CONTEXT` | `""` | [已废弃] 旧全局兜底 functionMap；检查点 5 起批次执行不再回落全局，此项不再被执行读取 |

**鉴权：内网通用，不带 token**（已确认）。AI Phone 接口本身可能声明鉴权，但内网部署放行。

安全边界：这个假设只适用于受控内网。开源部署时不要把 AI Phone 或 case-flow 回调入口直接暴露到公网；如果必须跨网络访问，需要在网关、反向代理或企业侧适配层增加鉴权、来源限制和访问日志。

## 设备就绪（查设备）

case-flow `GET /api/v1/aiphone/devices` 同时调 AI Phone 两个接口求交：
- `GET {AIPHONE}/api/devices/available` → **空闲且就绪**（online + ready + 未占用 + agent 在线）。标 `occupancy=idle`。
- `GET {AIPHONE}/api/devices/statuses` → 全量（**对外暴露版，字段与内部 `/api/devices` 等价**：serial/effective_status/platform/brand/model/os_version/screen_width/screen_height/last_seen_at/lock）；其中 `effective_status==busy`（在线被占用）→ 标 `occupancy=busy`（归一字段：`os_version→osVersion` 等）。
- 注：部署方可能不开放 `/api/devices`；case-flow 使用公开的 `/api/devices/statuses`。全量接口拿不到时只显示已确认空闲的设备（`source=service-idle-only`），不会把未知设备猜成可用。
- 未就绪/离线的不返回。全量接口失败时降级只返空闲（`source=service-idle-only`）。
- 占用中也可选（只是提示，选了会进 AI Phone 排队）。详见 `执行队列与设备就绪状态方案.md` §3/§7。

## 提交执行（submit）

`POST {AIPHONE}/api/submissions`，请求体（v1.7 形态）：

```json
{
  "submissionName": "...",
  "callbackUrl": "{PUBLIC}/api/v1/aiphone/callback/{token}",
  "cacheMode": "off",
  "retryMax": 0,
  "functionMapContext": "...(可选, 一级目录合并文本)",
  "items": [
    {
      "caseId": "cf-<case.id>",
      "caseName": "...",
      "runContent": "测试标题/前置条件/操作步骤/预期结果 拼接文本",
      "platforms": ["android"],
      "deviceAliasPools": {"android": ["A1","B1"]}
    }
  ]
}
```

要点：
- **runContent**：四段中文模板（测试标题 / 前置条件 / 操作步骤 / 预期结果）。
- **platforms**：`android` / `ios` / `harmony`；当前 case-flow 一条 case 走一个平台（默认从设备池 key 推导，缺省 `android`）。多平台暂不做。
- **deviceAliasPools**：`{平台:[别名]}`。缺省/`[]`=全池任挑；`["A1"]`=锁单台；`["A1","B1"]`=子集池。case-flow 整批共享一个池，由 AI Phone 调度器决定哪台空闲跑哪条。
- **cacheMode**：`off/v1/v2/v3`（轨迹缓存）。批量队列可选，默认 off；需 AI Phone 服务端开缓存能力否则回落 off。
- **retryMax**：失败重试上限，默认 0。
- **顺序**：items 数组顺序 = 前端队列顺序；实际执行/并发由 AI Phone 调度。

提交成功后 case-flow 落 `aiphone_execution_batches` + 每个 `aiphone_execution_items`，并把相关 case 置 `running`、清旧报告/失败/修复草稿、记 `active_execution_batch_id`/`external_submission_id`。

返回需含 `submissionId`（或 `id`），否则报错。

## functionMap（执行上下文）

> [当前] 批次执行（AI Phone/Web/API/Hybrid）的顶层 `functionMapContext` 由「资产库 + 挂载」编译得到，不再读旧 `function_map_files`：`compile_top_level_context` 取当前执行容器（case → 二级需求 + 其一级目录）的显式挂载资产，按执行器适用端过滤、一级/二级重叠去重，无匹配则不带（**去掉全局兜底**，无 8000 上限）。提 bug、修复和助手对话等非批次路径也读取同一套挂载编译结果。资产模型见 [Function Map](Function-Map.md)，字段合成与透传见 [Function Map 执行上下文协议](function%20map%20skills化执行单元协议.md)。

- 顶层来源：`case → 二级需求(source_requirement_item_id) → 一级目录`，取二级需求 + 一级目录的显式挂载资产（新模型），拼接成一段 `functionMapContext` 注入 submission。
- 适用端过滤：AI Phone 只带适用端含 `app` 的 Map（Web→web、API→api、Hybrid→app/web/api 任一）；不匹配的排除。
- 去重：同一资产同时挂在一级目录和二级需求时只带一次。
- 无显式挂载或全被过滤 → 不带 `functionMapContext`，不回落任何全局兜底。
- 单条 + 批量都自动携带，无需每次选择。

## 回调（接收结果）

AI Phone 调 `POST /api/v1/aiphone/callback/{callback_token}`。case-flow（`apply_aiphone_callback`）：
- 有 `caseId`/`event=submission.item.terminal` → **单条事件**：按 `(callback_token|submission_id) → batch`，再 `(external_case_id, platform) → item`，更新 item 与对应 `case_work_item`。
  - 状态映射：`success/passed/pass → passed`；`failed/cancelled/expired/timeout/error → failed`；其余 → `running`。
  - 失败写 `failure_summary`、`attention_reason=执行失败`；非失败清修复草稿。
- 否则 → **批次事件**：更新 batch 状态、`finished_at`、`summary_report_url`。
- 报告 URL 会被 `_normalize_aiphone_urls` 补成绝对地址。

## 关键设计决策

- **不主动轮询/对账**：状态只由回调或手动改推进（“最后一次覆盖”）。回调丢了就停在 running，需手动改——这是有意简化，**不要加 reconcile**。
- **外部 case id 格式 `cf-<id>`**，回调按它反查。
- AI Phone 侧能力（轨迹缓存 v1/v2/v3 语义、function_map_context_enabled、设备 lock/readiness 细节）以 AI Phone 仓库为准；case-flow 只消费协议。

## 接入内网 AI Phone

改两个 env（`CASE_FLOW_AIPHONE_BASE_URL` 指向服务地址；`CASE_FLOW_PUBLIC_BASE_URL` 指向 AI Phone 能访问到的 case-flow 地址）→ 重启后端（`get_settings()` 有 lru_cache）。内网部署按当前协议无需鉴权；公网或跨团队网络必须自行加网关鉴权/来源限制。详见 `开发与运维.md`。
