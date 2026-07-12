# AI Web 集成

<!-- RELATED_REPOSITORY_SLOT: 发布前补入维护者确认的 AI Web 公开仓库链接。 -->

状态：标准/快速模式调用与回调已接入；报告诊断、提交 bug 的 Web 语境调优后续继续迭代

case-flow 把 **App 类**用例（`execution_target=app`）发给 AI Phone 执行。AI Web 是"**浏览器版的、精简的 ai-phone**"，对外契约与 ai-phone **刻意同构**，用来补上 **Web 类**用例（`execution_target=web`）的自动执行——以前 web 泳道只能人工点。

本文记录 AI Web 接入 Case Flow 的当前公开契约：配置、选路与分发、提交与回调、浏览器覆盖泳道、报告读取和助手模式。AI Phone 的同构协议见 [AI Phone 集成](AI-Phone集成.md)。

## 这是什么 / 接在哪

- AI Web：自然语言驱动内置 Playwright 浏览器执行 Web 自动化，产出自包含 HTML 报告，Webhook 回调结果。无用例/项目/步骤等概念，只有"队列 / 并发 / 执行记录 / 接口与回调"。
- 与 ai-phone 的对外契约同构（提交体、设备端点、回调事件名与字段、匿名鉴权、报告绝对 URL 全一致），差异都被 AI Web 那边吸收（见"与 ai-phone 差异对照"）。
- 接入 case-flow 的**三处**：
  1. **标准执行 + 批量执行**：`execution_target=web` 的 case 路由到 AI Web。
  2. **助手对话模式**：作为能力工具箱里的 Web 执行工具（`tool_key=aiweb_dispatch`），与 AI Phone、AI API 及部署方自行增加的其他可选 Tool 并列，见 `助手对话模式方案.md`。
  3. 顺带把 **web 覆盖泳道**（Chrome / Safari / Firefox）从"仅人工点"变成"可自动执行点亮"。

## 接入原则：轻抽象，不复制编排

> 与 quick 模式的"完全复制独立"哲学不同。原因：在 case-flow 这边，app 和 web 只是同一条 case 的不同**端泳道**，`case_work_items` 的状态语义（执行状态、覆盖、聚合、诊断触发、生命周期）**完全一样**，差的只是底层 HTTP 端点和 `platform`。复制 `executions.py` 那套多端聚合/覆盖/诊断编排只会制造重复且易漂移。

落地方式：把 ai-phone 协议里**纯无状态的客户端动作**抽成一个执行器接口，AI Phone / AI Web 各实现一份；**上层 case 状态编排（聚合、覆盖、诊断、生命周期）仍在 `executions.py` 共用一套**。

把 `app/executors/`（现为 Protocol 占位）接上线：

```python
class DeviceExecutor(Protocol):
    async def list_devices(self) -> AIPhoneDeviceListOut: ...
    async def submit(self, request_payload: dict, callback_url: str) -> dict: ...  # 返回归一后的 response
    # 回调入参解析复用同一套 apply_*_callback（事件名/字段同构，无需各写一份）
```

- `AiPhoneExecutor`：打 `aiphone_base_url`，platform 走 android/ios/harmony。
- `AiWebExecutor`：打 `aiweb_base_url`，platform 走 Chrome / Safari / Firefox；AI Web 外部规范名里的 `webkit` 在 case-flow 边界固定映射为 `safari`，不新增一套 WebKit 泳道。
- 选哪个执行器由 `execution_target` 决定（见"选路与分发策略"）。
- 回调：因为事件名/字段同构，`apply_aiphone_callback` 的解析逻辑可直接共用；只是回调 URL 前缀 token 路由分两条，落库时记下 `executor` 便于报告读取选 reader。

> 注意：这是对 AGENTS.md "执行器尚未抽象、先具体后抽象" 的有意推进——现在出现了第二个同构执行器，正是抽象回本的时机。抽象只覆盖"客户端动作 + 配置选址"，不动状态编排。

## 选路与分发策略：按 execution_target 分组推送

- `execution_target=app` → AI Phone（android/ios/harmony）。
- `execution_target=web` → AI Web（chrome / safari / firefox）。
- `api` / `manual` → 不自动投递（保持现状）。

业务上，用户的一次"本次执行队列"**可以混合** App / Web / API / 人工 case。混合不是错误，而是分发策略：

- 用户视角：一次队列、一次确认、一个本次执行顺序。
- 系统视角：按执行器分组，分别生成外部 submission：
  - App 子集 → AI Phone submission。
  - Web 子集 → AI Web submission。
  - API 子集 → 内置 AI API 本地 submission。
  - 人工子集 → 暂不外部投递，只更新本地执行态。
- 执行顺序：队列顺序在每个执行器子集内保留；不同执行器之间天然并行，最终靠各自回调落状态。
- 设备/槽策略分开：AI Phone 需要用户选择 deviceAliasPools；AI Web 展示并选择浏览器槽，按 Chrome / Safari / Firefox 分组提交。**同一浏览器引擎内槽位等价**，但跨浏览器是真实不同的执行端。

> 当前代码已按这个模型分发：前端队列能混合展示 AI Phone / AI Web / AI API / 人工，确认时分别提交 App、Web、API 子集，人工只更新本地执行态。

## 推送策略 vs prompt 策略

这里要分清两层：

1. **外部端推送策略**：case-flow 决定"把哪些 case 推给哪个执行器、用什么端/设备/槽、带什么 callback、如何落批次映射"。
2. **执行内容 prompt 策略**：case-flow 把 case 内容拼成 `runContent`，再把 functionMap 合并成 `functionMapContext`，交给外部执行器内部模型理解和执行。

AI Phone 现在不是通过一整段可配置 prompt 决定"推送到哪里"；推送策略主要由 case-flow 显式字段控制：

- `deviceAliasPools`：选择手机平台和设备池，间接决定 `platforms`。
- `items[]` 顺序：保留本次队列里 App 子集的顺序。
- `cacheMode` / `retryMax`：执行策略参数。
- `callbackUrl`：回调入口。
- `runContent` + `functionMapContext`：给执行器模型看的任务内容和业务上下文，不负责 case-flow 侧分发。

AI Web 的推送策略应保持同构但更轻：

- `platforms` 由用户选择的浏览器类型生成，默认 `["chrome"]`；可为 `["chrome"]`、`["firefox"]`、`["safari"]` 或多选组合。
- `deviceAliasPools` 由前端浏览器槽选择生成，形如 `{ "chrome": ["Chrome #1"], "safari": ["Safari #1"], "firefox": ["Firefox #1"] }`。AI Web 同一引擎内忽略具体槽别名，但按 `platforms` 真实选择浏览器引擎。
- `items[]` 顺序保留 Web 子集的队列顺序。
- `cacheMode` / `retryMax` 可先传默认值；AI Web 不支持时忽略。
- `runContent` + `functionMapContext` 继续复用同一套内容拼接口径。

因此，外部端推送策略**需要按执行器定制**，但定制点很小：base_url、callback path、默认 platform、设备/槽选择方式、是否使用 cache/retry。case 状态编排、回调解析、覆盖聚合、诊断和 bug 不应复制。

## 配置（env，前缀 `CASE_FLOW_`）

| env | 默认 | 说明 |
|---|---|---|
| `CASE_FLOW_AIWEB_BASE_URL` | `http://127.0.0.1:8009` | AI Web 地址：查浏览器槽 + 提交都打这里 |
| `CASE_FLOW_AIWEB_CALLBACK_BASE_URL` | 无（本地示例 `http://127.0.0.1:8800`） | AI Web 回调地址前缀，必须显式配置成 AI Web 能反向访问到的 case-flow 地址 |
| `CASE_FLOW_FUNCTION_MAP_CONTEXT_*` | 见 AI-Phone | 旧 functionMap 全局兜底/上限 [已废弃]；执行改读新挂载编译，无 8000 上限、无全局兜底 |

- 鉴权：内网匿名，不带 token（与 ai-phone 一致）。若 AI Web 部署设了 `AIWEB_API_TOKEN`，case-flow 需带 `Authorization: Bearer`——MVP 内网默认不设。
- `get_settings()` 有 lru_cache，改 env 后重启后端。

## 设备就绪（查浏览器槽）

AI Web 暴露与 ai-phone 同形端点，把"并发槽"投影成 N 个浏览器（N=并发配置）：

- `GET {AIWEB}/api/devices/available` → 空闲槽（字段精简：serial/alias/platform/screenWidth/screenHeight）。
- `GET {AIWEB}/api/devices/statuses` → 全量槽（含 `effectiveStatus` idle/busy，字段与 ai-phone 等价）。
- 槽位来自 AI Web 的 `AIWEB_BROWSER_SLOTS`，如 `chrome:2,firefox:1,webkit:1` 会投影成 Chrome / Firefox / Safari 三组槽。
- AI Web 以 `webkit` 表示 Safari 引擎；case-flow 对外展示、覆盖和落库统一用 `safari`。
- **同一引擎内选哪个槽对执行无影响**（调度器任挑空闲）；**跨引擎是真实不同浏览器**，必须作为不同 platform 提交。
- case-flow 既有 `list_aiphone_devices()`（求交 available + statuses busy）逻辑可原样复用，仅换 base_url → 由 `AiWebExecutor.list_devices()` 提供。

## 提交执行（submit）

`POST {AIWEB}/api/submissions`，请求体与 ai-phone 同构。注意这是**Web 子集**的外部 submission，不等于用户界面上的混合执行队列整体：

```json
{
  "submissionName": "...",
  "callbackUrl": "{AIWEB_CALLBACK_BASE}/api/v1/aiweb/callback/{token}",
  "cacheMode": "off",
  "retryMax": 0,
  "functionMapContext": "...(可选, 显式挂载编译的顶层上下文，带资产边界)",
  "items": [
    {
      "caseId": "cf-<case.id>",
      "caseName": "...",
      "runContent": "测试标题/前置条件/操作步骤/预期结果 拼接文本",
      "platforms": ["chrome", "safari", "firefox"],
      "deviceAliasPools": {
        "chrome": ["Chrome #1"],
        "safari": ["Safari #1"],
        "firefox": ["Firefox #1"]
      }
    }
  ]
}
```

要点（与 ai-phone 的差异）：

- **platform = 浏览器类型**。支持 `chrome` / `firefox` / `safari`；AI Web 回调或响应里的 `webkit` 在 case-flow 边界规范成 `safari`。
- **deviceAliasPools / cacheMode**：`deviceAliasPools` 来自用户选择的浏览器槽；AI Web 同一引擎内忽略具体槽别名，但按 `platforms` 真实选择引擎。`cacheMode` 当前可忽略。
- **functionMapContext**：AI Web 支持，注入 system prompt。顶层由当前二级需求 + 其一级目录的显式挂载编译（端过滤 web、去重、带资产边界），无 max_chars 拒绝、无全局兜底；口径与 AI Phone 一致。
- **assets**：AI Web 支持引用其素材库文件（执行中 upload_file 用）。case-flow 当前无此需求，先不接；未来要传文件再说。
- **runContent**：仍是四段中文模板。
- 响应须含 `submissionId`；每个 item 带 `platform`。落库复用 `aiphone_execution_batches/items`，用已有 `executor` 字段区分 `ai_phone` / `ai_web`。

### 调用策略放在哪里

当前 AI Phone 调用策略分散在两层：

- 前端入口层：决定本次队列里哪些是 App，读取设备，组 `deviceAliasPools`，然后调用 `/executions/aiphone/submit`。
- 后端 service 层：拼 runContent、解析 functionMap、生成 callback token、提交外部服务、落批次表和执行单元、把 case 置为 running。

接 AI Web 时不建议把完整业务编排复制一份。更合适的边界是：

- 前端继续承载"用户选择策略"：App 选设备池；Web 选 Chrome / Safari / Firefox 浏览器槽；API/人工仍本地。
- 后端承载"执行器提交策略"：同构协议、base_url、AI Web 专属 callback_url、默认 platform、URL 归一、批次落库、回调归一。
- 如果后续接入更多执行器，再考虑新增统一混合 dispatch 接口，由后端一次收 mixed queue 并 fan-out；MVP 先保持现有入口形态，减少改动。

## 回调（接收结果）

AI Web 侧按目标契约会调 `POST /api/v1/aiweb/callback/{callback_token}`，事件名与字段**与 ai-phone 完全一致**。本次 case-flow 接入要补上的内容是：提供 aiweb callback 路由，并在 Web submission 提交时落批次/执行单元映射。

- 单条终态 `submission.item.terminal`：带 `caseId` + `platform`（chrome/firefox/webkit）。case-flow 先把 `webkit` 规范为 `safari`，再按 `(caseId, platform)` 匹配 item。`state` 为 `success` / `failed`，`statusReason` 含 `needs_human`（模型请求人工）等。`reportUrl` 已是绝对地址。
- 批次终态 `submission.terminal`：带 `submissionState` / `counts` / `summaryReportUrl`。
- 状态映射、失败诊断触发、覆盖聚合 **共用 `apply_aiphone_callback` 那套**（事件同构）。报告读取按 `executor=ai_web` 选 reader（见报告读取）。

## 覆盖泳道：web 转"可执行点亮"

- 已把可自动点亮的泳道扩成包含 web（chrome / safari / firefox），即 AI Web 回调终态也点亮对应 web 泳道，参与卡片整体判定（worst-wins）与诊断融合，逻辑与 app 端一致。
- `executions.py` / `quick_executions.py` 使用 `COVERAGE_EXEC_LANES` 按执行器给出该执行器负责的泳道集合，避免硬编码 app 三端。
- 覆盖泳道全集已是 `{android, ios, harmony, chrome, safari, firefox}`（见 `quick_importing.COVERAGE_LANES`、`端覆盖标记方案.md`），无需扩字段。

## 报告读取

- `report_readers/` 现有 ai-phone reader。AI Web 报告是自包含 HTML（每步截图/Thought/动作/结果），结构与 ai-phone 报告相近但不必相同。
- 现阶段沿用现有 HTML reader 读取 AI Web 报告，并把 reader 标记为 `ai_web`。后续若 AI Web 报告结构分化，再在 `report_readers/` 增加 `ai_web` 专用分支。
- 诊断/提 bug 复用同一条 `case_repair` / `bug_submit` 业务路径；错误修复不再读取端侧 prompt TXT，而是由后台编排固定三段：失败锚点与需求来源 → 根因取证/滑窗补证 → 修复门禁与候选生成。AI Web 与 AI Phone 使用同一套后台门禁。滑窗不是固定执行：只有根因阶段模型提出 `need_more=true` 时，后台才按问题补充轨迹窗口/截图进入下一轮。

## 助手对话模式接入

- AI Web 是助手能力工具箱里的 Web 执行工具（`aiweb_dispatch`），与 `aiphone_dispatch`、`aiapi_run` 及部署方自行增加的其他可选 Tool 并列。
- 助手侧不区分标准/quick 的 case 语义，它只是"调浏览器办事"；`agent_dispatches.tool_key=aiweb_dispatch`，`tool_kind=external_executor`。
- 复用同一个 `AiWebExecutor` / 同构 HTTP 客户端能力（设备查询 / 提交 / 回调归一），助手与标准执行共享底层执行器，不共享业务状态编排。
- 详见 `助手对话模式方案.md`。

## 与 ai-phone 差异对照（接入时只需吸收这几条）

| 维度 | ai-phone | AI Web | case-flow 接入处理 |
|---|---|---|---|
| 基址 | `:8000` | `:8009`（`CASE_FLOW_AIWEB_BASE_URL`） | 执行器选址 |
| `platform` | android/ios/harmony | chrome/firefox/webkit(safari) | 提交按所选浏览器 fan-out；`webkit` 入库映射为 `safari` |
| 设备端点 | 真机列表 | 每浏览器引擎独立台数投影成槽 | `list_devices()` 复用，仅换 base_url 并规范平台 |
| `deviceAliasPools` | 影响派发 | 同引擎内具体槽位忽略，跨引擎真实生效 | 前端按浏览器分组选槽后传递 |
| `cacheMode` | v1/v2/v3 | 收下即忽略 | 传 off 即可 |
| `functionMapContext` | 支持 | 支持 | 解析口径一致 |
| `assets` | 无 | 支持 | 当前不接 |
| 提交/查询/取消/回调/匿名 | — | 同构 | 客户端动作复用、回调解析共用 |

## 当前实现状态

后端：
- [已实现] `core/settings.py`：加 `aiweb_base_url`（env `CASE_FLOW_AIWEB_BASE_URL`，默认 8009）和 `aiweb_callback_base_url`（env `CASE_FLOW_AIWEB_CALLBACK_BASE_URL`，AI Web 提交时必填）。
- [已实现] `services/executions.py` / `services/quick_executions.py`：AI Phone / AI Web 共用提交与回调编排；AI Web 按独立 env 配置 base_url 与 callback base，默认 platform、coverage 泳道；AI Web 支持 Chrome / Safari / Firefox 三浏览器，`webkit` 映射到 `safari`。
- [已实现] `api/v1/routes/workbench.py` / `routes/quick.py`：新增 `GET /aiweb/devices`、`POST /executions/aiweb/submit`、`POST /aiweb/callback/{token}`。
- [已实现] 批次落库：复用 `aiphone_execution_batches/items` 与 `quick_execution_batches/items`，用 `executor` 区分 `ai_phone` / `ai_web`；迁移 `0021` 把 submissionId 唯一约束改为 `(executor, submission_id)`。
- [已实现/待调优] `case_repair.py` / `quick_repair.py`：AI Web 失败诊断接入后台分阶段修复编排器；不再按端读取 Web 专用 prompt 文件。Web 语境下的报告读取和提 bug 形态后续继续调。
- [待验证/调优] `report_readers/`：现有 HTML reader 理论上可读 AI Web 自包含报告；后续按真实报告结构决定是否拆 `ai_web` reader。

前端：
- [已实现] 执行入口按 `execution_target` 分组推送：App 子集调 AI Phone，Web 子集调 AI Web，API 子集调 AI API，人工暂走本地状态。
- [已实现] 同一执行队列允许混 App/Web/API/人工，不拦截；确认后 fan-out 到不同执行器。
- [已实现] Web 单条/批量执行展示并选择 AI Web Chrome / Safari / Firefox 浏览器槽。
- [已实现] Web 回调终态会点亮对应浏览器覆盖泳道（复用既有泳道 UI）。

文档：
- [已实现] `AI-Phone集成.md` 指向本文；`API契约.md` 加 aiweb 路由；`数据模型.md` 标注 `aiphone_execution_batches/items` 为同构端执行批次表；`产品方向、功能细节与流转总参考.md` 把 AI Web 从"本地 running"更新为真实外部执行；`索引.md` 已登记本文。

## 关键设计决策（已对齐）

- **轻抽象**：抽执行器客户端层（device/submit/回调归一），case 状态编排共用一套，不复制 `executions.py`。
- **按 `execution_target` 分发**：用户队列可混合；系统按执行器分组，app→AI Phone，web→AI Web，api→AI API；人工暂本地。
- **先接调用与回调**：标准/快速模式先接通 AI Web submit/callback；报告诊断、提交 bug 的 Web 语境形态后续继续打磨。

## 已知限制与后续验证

- `report_readers` 是否需要 `ai_web` 分支（先验证现有 html reader 兼容性）。
- web 失败的报告读取/提 bug 是否需要 Web 专用分支；诊断修复策略当前统一走后台编排器，不再走 prompt TXT。
- 设备槽展示文案（chrome 槽 vs 真机）的细节继续打磨。
- AIWEB_API_TOKEN 部署场景下 case-flow 的带 token 适配（内网默认匿名）。
