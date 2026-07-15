# AI API 集成

状态：方案已对齐；标准工作流与 quick 前后端已接入，已支持单请求与 `scenarios[]` 语义化多变体

Case Flow 计划把 **API 类**用例（`execution_target=api`）交给内置 **AI API** 执行器执行。AI API 不独立部署服务：它没有真机、浏览器槽、设备锁、远程调度这些资源问题，本质是“模型把自然语言 case 编译成受控 HTTP 请求计划，Case Flow 后端执行请求，再生成报告并回落状态”。

本文记录 AI API 的定位、流程、执行计划结构、报告、失败分类、执行边界和落地改动点。AI Phone / AI Web 的外部同构执行器见 `AI-Phone集成.md`、`AI-Web集成.md`。

## 这是什么 / 接在哪

- AI API：Case Flow 内置的轻执行器，用自然语言 case + functionMap/API 上下文生成 HTTP 执行计划，执行请求，断言响应，产出报告。
- 它不是独立服务，不提供设备端点，不需要 `/api/devices/*`，也不需要外部 callback。
- 它仍然是“执行器”，不是一个自由 curl 工具：输入是 case，输出是 batch/item/report/status。
- 接入范围使用同一套 AI API kernel 和报告格式；标准工作流落正式执行表，quick 落 quick 专属执行表，避免跨模式复用业务状态。

## 核心设计哲学

AI API 要和 AI Phone / AI Web 在产品语义上统一，但实现上更轻：

| 维度 | AI Phone / AI Web | AI API |
|---|---|---|
| 执行资源 | 真机 / 浏览器槽 | 后端一次 HTTP 请求或请求链 |
| 调度 | 外部执行器排队和派发 | 无设备调度，Case Flow 后台任务直接执行 |
| 提交形态 | submission + items | 仍保留 batch + items |
| 回调 | 外部 webhook 回调 Case Flow | 内部生成同形终态事件或直接调用同一落库函数 |
| 报告 | HTML 报告 URL | HTML 报告 URL，失败也必须有 |
| 平台 | android/ios/harmony/chrome/safari/firefox | 固定 `api` |
| executor | `ai_phone` / `ai_web` | `ai_api` |

关键原则：

1. **模型不是直接执行者**：模型只负责把自然语言 case 编译成结构化计划，并参与断言裁决；真正 HTTP 请求由后端受控执行。
2. **缺信息即失败**：执行器不是聊天助手，不追问用户。模型判断缺少 URL、鉴权、参数、断言依据时，直接生成失败报告和修复建议。
3. **失败也有报告**：即使没有发出 HTTP 请求，也要有 `compile_failed` 报告，后续诊断修复才能接上。
4. **Case Flow 仍是业务事实源**：AI API 只推进 `case_work_items` 状态，不引入第二套业务状态。
5. **执行边界明确**：模型只能产出结构化 HTTP plan，不能产出 shell/curl；URL allowlist、方法、header、超时等作为部署侧可选收紧配置。响应大小上限只作为部署侧可选配置，默认完整保留执行证据。
6. **支持语义展开**：自然语言 case 可以描述测试意图，而不是只描述一次固定请求。模型需要把“完成 CRUD 测试”“做边界值测试”“超过 1000 要失败”这类语义展开成多条可执行请求变体，并按每条变体的预期语义判断通过或失败。

## 执行流程

```text
用户选择 API case
  -> 标准工作流 POST /api/v1/executions/aiapi/submit
     quick 工作流 POST /api/v1/quick/executions/aiapi/submit
  -> 后端创建 batch/item，case 置 running
  -> 后台任务读取 case 四段内容 + functionMap/API 上下文
  -> 模型编译 APIExecutionPlan
      -> executable=false：生成 compile_failed 报告，item failed
      -> executable=true：生成单请求或多变体请求计划
  -> 后端按计划执行 HTTP 请求
  -> 规则断言 + 必要时模型裁决每个变体响应是否符合预期语义
  -> 生成 HTML 报告
  -> 内部回落 item 终态和 case 状态
  -> 失败时触发既有诊断/bug 草稿链路
```

与 AI Phone / AI Web 一致，提交后先把 case 置为 `running`，旧报告、失败摘要、修复草稿、bug 草稿进入新一轮清理。不同点是 AI API 的“回调”由本进程内部完成，不依赖外部网络反向访问。

## 内部停止

标准和快速模式的 AI API batch 都有本进程内停止入口 `stop_aiapi_execution(submission_id, case_ids?)`。首页 / Quick 的停止操作会在清除本地执行关联前取出该 batch，然后在本地 case 已恢复 `not_run` 后异步调用这个入口。它取消当前执行 coroutine，并跳过后续 case；停止的单元不生成报告、不回写成功/失败终态、不触发诊断。这个入口**不新增 HTTP 路由**，也不适用于 `POST /api/v1/aiapi/run` 的对外 Direct Run。

已经发出的 HTTP 请求没有通用的远端撤销协议：停止只中断 Case Flow 当前等待和后续调用，不推断目标服务是否已经处理请求或是否成功。这是明确暴露的链路边界，不会把未知结果兜底成某个执行状态。

## API 执行计划

模型输出必须是结构化 JSON，禁止让模型输出 shell、curl 字符串或自由文本请求。

### 可执行计划

```json
{
  "executable": true,
  "reason": "信息充足，可以执行登录接口验证。",
  "request": {
    "method": "POST",
    "url": "https://api.example.com/login",
    "headers": {
      "Content-Type": "application/json"
    },
    "query": {},
    "body_type": "json",
    "body": {
      "phone": "13800000000",
      "password": "test123"
    },
    "timeout_seconds": 15
  },
  "assertions": [
    {
      "type": "status_code",
      "expected": 200
    },
    {
      "type": "json_path_exists",
      "path": "$.token"
    }
  ],
  "notes": [
    "根据前置条件中的测试账号生成请求体。",
    "预期结果要求返回 token，因此增加 token 存在断言。"
  ]
}
```

### 语义化变体计划

当 case 不是单次请求，而是一个测试意图时，模型需要输出 `scenarios`，每个 scenario 表示一个独立请求变体。典型来源包括：

- **CRUD 语义**：用户说“完成这个接口的增删改查测试”，模型需要生成创建、查询、修改、删除以及必要的前后置校验请求。
- **边界值语义**：用户说“参数不能大于 1000，测试超过边界值”，模型需要生成 `1000`、`1001` 等请求变体。
- **异常入参语义**：用户说“测试必填、空值、非法类型”，模型需要生成缺字段、空字符串、类型错误等请求变体。
- **等价类语义**：用户只描述业务约束时，模型可按有效值、无效值、临界值拆分请求。

示例：

```json
{
  "executable": true,
  "reason": "已识别为 limit 参数边界值测试，最大允许值为 1000。",
  "scenarios": [
    {
      "id": "limit_eq_1000",
      "name": "边界内最大值应成功",
      "intent": "验证 limit=1000 仍被接口接受",
      "expected_outcome": "accepted",
      "request": {
        "method": "POST",
        "url": "https://api.example.com/orders/query",
        "headers": {"Content-Type": "application/json"},
        "body_type": "json",
        "body": {"limit": 1000},
        "timeout_seconds": 15
      },
      "assertions": [
        {"type": "status_code", "expected": 200}
      ]
    },
    {
      "id": "limit_gt_1000",
      "name": "超过最大值应被拒绝",
      "intent": "验证 limit=1001 被接口拦截",
      "expected_outcome": "rejected",
      "request": {
        "method": "POST",
        "url": "https://api.example.com/orders/query",
        "headers": {"Content-Type": "application/json"},
        "body_type": "json",
        "body": {"limit": 1001},
        "timeout_seconds": 15
      },
      "assertions": [
        {"type": "status_code", "expected": 400},
        {"type": "body_contains", "contains": "不能大于1000"}
      ]
    }
  ],
  "notes": [
    "limit=1000 是合法边界，接口接受代表该变体通过。",
    "limit=1001 是非法越界，接口拒绝代表该变体通过；如果接口仍接受，则该变体失败。"
  ]
}
```

`expected_outcome` 是报告和裁决的关键语义，不等同于 HTTP 请求是否“成功发出”：

| expected_outcome | 含义 | 通过条件示例 |
|---|---|---|
| `accepted` | 合法请求应被接口接受 | HTTP 2xx，或业务 code 表示成功 |
| `rejected` | 非法请求应被接口拒绝 | HTTP 4xx，或业务 code/message 明确表示参数错误 |
| `changed` | 写操作应产生状态变化 | 创建/修改后查询到目标状态 |
| `unchanged` | 非法写操作不应污染数据 | 请求被拒绝，或后续查询确认数据未变化 |

如果用户说“超过边界值测试”，测试目标不是“请求返回 2xx 就通过”，而是“接口必须正确拒绝越界值”。因此 `1001` 请求被拒绝时该变体是通过；`1001` 被接受时该变体是失败。

### 滑窗上下文与链式步骤

当同一个 scenario 内后续请求依赖前序响应时，AI API 支持 `steps[]` 顺序执行。执行器会为每个 scenario 维护一个滑窗式变量上下文：

1. step 执行前，用当前变量表替换 request 中的 `{{变量名}}`。
2. step 请求完成后，按 `extract` 的 JSONPath 从响应体提取变量。
3. 提取出的变量进入下一步上下文。
4. 如果变量缺失、提取失败或断言失败，当前 scenario 立即失败，后续 step 不再硬跑。
5. 替换变量后的真实请求仍要重新做执行前校验，不能绕过已配置的 allowlist 和方法限制。

模型判断原则不是固定 CRUD 模板，而是按用户语义决定数据来源：

- 用户明确给了数据，就使用用户给定数据。
- 用户没有给数据，但前序步骤会产生该数据，就从前序响应提取并引用。
- 用户没有给数据，前序步骤也不会产生该数据，则判定不可执行，不能猜一个 ID。

示例：用户说“新增一个用户，然后查询、修改、删除这个用户”。用户没有给 userId，但“这个用户”来自新增步骤，因此模型可以输出：

```json
{
  "executable": true,
  "reason": "新增后查询、修改、删除同一个用户，需要从创建响应中提取 userId。",
  "scenarios": [
    {
      "id": "user_crud",
      "name": "用户增删改查",
      "intent": "创建用户后用返回 ID 完成查询、修改、删除",
      "expected_outcome": "changed",
      "steps": [
        {
          "id": "create_user",
          "request": {
            "method": "POST",
            "path": "/users",
            "headers": {"Content-Type": "application/json"},
            "body_type": "json",
            "body": {"name": "Alice"},
            "timeout_seconds": 15
          },
          "assertions": [{"type": "status_code", "expected": 201}],
          "extract": {"userId": "$.data.id"}
        },
        {
          "id": "query_user",
          "request": {
            "method": "GET",
            "path": "/users/{{userId}}",
            "headers": {},
            "body_type": "none",
            "timeout_seconds": 15
          },
          "assertions": [{"type": "status_code", "expected": 200}]
        }
      ]
    }
  ]
}
```

这不是复杂 workflow 引擎：当前只支持顺序 step、简单 JSONPath 提取和 `{{变量名}}` 替换，不做循环、分支和并发。

### 不可执行计划

```json
{
  "executable": false,
  "failure_type": "compile_failed",
  "reason": "缺少接口 base_url、请求路径和鉴权方式，无法构造可执行 HTTP 请求。",
  "repair_suggestion": "请在前置条件或 functionMap 中补充接口域名、路径、鉴权 header，以及必要请求参数。"
}
```

### 计划字段约束

- `method` 默认允许 `GET/POST/PUT/PATCH/DELETE`，部署侧可用配置收紧。
- `url` 可以直接给完整地址；如果模型只给 `path`，则由配置的 `base_url + path` 组合。allowlist 为空时不限制 host，只有显式配置 allowlist 时才按 allowlist 限制。
- `headers` 需过滤危险头：`Host`、`Content-Length` 等由 HTTP client 自己生成。
- `body_type` 先支持 `none/json/form/raw`，文件上传暂不做。
- `timeout_seconds` 有全局上限，默认不超过 20 秒。
- 响应体和报告默认完整保留，便于内网执行复盘；如部署侧确实需要，可显式配置响应大小上限。
- 单请求计划使用 `request + assertions`；语义化用例使用 `scenarios[]`。每个 scenario 必须有 `expected_outcome`，并提供 `request + assertions` 或 `steps[]`。
- `steps[]` 只表示同一 scenario 内的顺序依赖请求；支持 `extract` 提取变量和 `{{变量名}}` 引用变量。
- 只要任一必需变体缺少请求参数、断言依据或前后置依赖，模型不能部分执行后假装成功；应输出不可执行或把缺失变体标为失败并给出修复建议。

## 上下文来源

AI API 需要比普通自然语言 case 更依赖上下文。推荐输入优先级：

1. case 四段内容：测试标题 / 前置条件 / 操作步骤 / 预期结果。
2. 一级目录 functionMap：只读执行参考，可以放 API base_url、接口路径、鉴权方式、测试账号、公共 headers、业务术语。
3. 全局 AI API 配置：可选 allowlist、默认 base_url、默认 headers、允许方法、超时，以及可选的响应大小上限。

AI API 的模型编排必须按这个权重执行：**用户本次任务 > 明确预期结果 > functionMap/API 上下文 > 全局默认配置**。functionMap 只负责补齐“怎么请求”，不能决定“这次要测什么”。如果 functionMap 很长，模型只抽取与本次任务直接相关的接口、字段和规则；不能因为 functionMap 里写了 CRUD、边界值或其他接口，就主动扩展本次 case 的测试范围。

当用户任务和 functionMap 冲突时，以用户任务为准；如果按用户任务仍无法确定 base_url、path、method、必要参数、鉴权或可验证预期，AI API 应输出不可执行，并在报告中给出缺失项和修改建议。

## 提交执行

建议新增路由：

```http
POST /api/v1/executions/aiapi/submit
```

请求体沿用现有提交风格，去掉设备池：

```json
{
  "caseIds": [123, 124],
  "submissionName": "Case Flow API 执行 2026-06-27 10:00:00"
}
```

后端行为：

- 只接受 `execution_target=api` 的 case。
- 创建执行 batch，`executor="ai_api"`。
- 每条 case 创建一个 item，`platform="api"`，`external_case_id="cf-<case.id>"`。
- 把 case 置为 `running`，清旧报告/失败摘要/诊断草稿/bug 草稿。
- 启动后台任务逐条执行；执行顺序按 items 顺序即可，MVP 可串行，后续再加有限并发。

数据表有两种选择：

1. **推荐 MVP**：复用 `aiphone_execution_batches/items`，继续用 `executor` 区分 `ai_api`。优点是报告浮层、状态聚合、诊断触发最少改动；缺点是表名历史包袱更重。
2. **后续整理**：把 `aiphone_execution_*` 迁移或别名化为更通用的 `executor_execution_*`。这属于命名治理，不阻塞 MVP。

## 内部终态事件

AI API 不需要真实 callback URL，但为了统一编排，内部可以构造与外部执行器同形的事件：

单条终态：

```json
{
  "event": "submission.item.terminal",
  "version": 1,
  "submissionId": "local-aiapi-xxx",
  "caseId": "cf-123",
  "caseName": "登录接口返回 token",
  "platform": "api",
  "engine": "ai-api",
  "state": "success",
  "statusReason": "assertion_passed",
  "runId": "aiapi-run-xxx",
  "reportUrl": "http://127.0.0.1:8800/media/aiapi/reports/xxx.html"
}
```

批次终态：

```json
{
  "event": "submission.terminal",
  "version": 1,
  "submissionId": "local-aiapi-xxx",
  "submissionState": "done",
  "counts": {"success": 1, "failed": 1},
  "summaryReportUrl": "http://127.0.0.1:8800/media/aiapi/reports/_summary.html"
}
```

实现上不一定真的走 HTTP callback；可以提取现有 `_apply_executor_callback` 的核心落库函数，内部直接调用。

## 状态与失败类型

AI API item 状态建议与外部执行器保持：

| item state | case 状态 | 含义 |
|---|---|---|
| `success` | `passed` | 请求执行成功，断言通过 |
| `failed` | `failed` | 编译失败、请求失败、响应断言失败或裁决失败 |
| `cancelled` | `failed` 或保留取消态映射 | 后续若支持取消，再细化 |

`statusReason` 建议先固定这些值：

| reason | 含义 |
|---|---|
| `compile_failed` | 模型认为信息不足，无法构造请求 |
| `plan_invalid` | 模型输出 JSON 不合法，或违反 schema |
| `security_blocked` | URL/method/header/body 触发部署侧执行限制 |
| `request_error` | DNS、连接、TLS、超时等请求层失败 |
| `http_error` | 状态码不符合预期 |
| `assertion_failed` | 响应可读，但断言不通过 |
| `assertion_passed` | 断言通过 |
| `model_failed` | 模型编译或裁决调用失败 |

报告读取层可把 `compile_failed/security_blocked/plan_invalid` 归入“case 描述或配置不可执行”，把 `request_error/http_error/assertion_failed` 归入“执行或业务结果失败”，供修复建议区分。

## 报告格式

AI API 必须生成自包含 HTML 报告，且失败也生成。当前报告样式必须贴近 AI Phone / AI Web：

- 使用统一的深色报告壳、顶部元信息 chip、中文状态徽标和请求明细结构。
- 首屏展示人能直接读懂的结论：通过/失败、中文结论原因、执行耗时、开始时间、结束时间、编排请求数、实际请求数、请求接口数、请求/响应大小、断言数、场景数。
- 不把 `status`、`statusReason`、`expectedOutcome` 这类内部英文执行字段直接作为主报告内容展示；主报告统一使用“执行结果”“结论原因”“预期语义”等中文标签。
- 报告主线按任务流展示：用户输入 → 执行概览 → AI 编排 → 请求与响应明细 → 失败修复建议 → 调试原文。
- 用户输入只展示标题、前置条件、操作步骤、预期结果；functionMap/API 上下文只显示“已加载/未加载”，不展示原文。
- AI 编排结果展示模型把自然语言转成了哪些请求、哪些 scenario/step、每步的参数和断言。
- 请求与响应明细按请求或 scenario 展示：每个步骤包含目标、请求地址、预期语义、HTTP 状态、请求耗时、开始/结束时间、请求大小、响应大小、请求参数、请求 Body、响应 Body、断言结果、失败说明和修复建议；Header 放到本步骤折叠区。
- 模型计划、执行前校验、请求/响应/断言原文保留在底部折叠区，登录账号、密码、token、Cookie 等真实执行参数默认完整展示，不做脱敏，但不展示 functionMap/API 上下文原文。

报告里不需要截图，但要让现有 HTML reader 能读到关键文本；后续如需要可以加 `report_readers/ai_api.py` 做更精准的结构化提取。

## 断言策略

MVP 建议“规则断言为主，模型裁决为辅”：

- 规则断言确定性强：`status_code`、`json_path_exists`、`json_path_equals`、`body_contains`、`header_exists`。
- 模型裁决只在自然语言预期无法完全规则化时参与，例如“返回错误信息语义上说明手机号格式错误”。
- 模型裁决必须看到：case 预期、执行计划、实际请求、实际响应、规则断言结果。
- 模型裁决不能改变实际请求，只能判断响应是否满足预期。
- 多变体计划按 scenario 独立判定，再聚合成 item 结论；只要任一必需 scenario 失败，整个 item 失败。
- 对 `rejected` 类变体，HTTP 4xx、业务错误码、错误 message 都可能是“测试通过”的证据；不能简单把所有非 2xx 都判为失败。
- 对 `accepted/changed` 类变体，HTTP 2xx 也不一定通过，还要检查业务 code、响应字段或后续查询结果。

如果模型无法给出稳定裁决，默认失败，报告里标 `model_failed` 或 `assertion_failed`。

## 执行边界与配置

AI API 保留开源部署需要的收紧开关，但默认关闭这些安全收紧行为，先满足内网执行器的本机/私网测试场景：

- **默认内网执行**：AI API 定位为内网执行器，默认允许请求 `localhost`、`127.0.0.1` 和私网地址，便于测试本机、测试环境和内部服务。
- **可选 URL allowlist**：allowlist 为空时不限制 host；只有部署侧显式配置 `allowed_hosts` 或 `allowed_base_urls` 时，才按配置限制可访问范围。
- **协议限制**：只允许 `http` / `https`。
- **方法限制**：默认允许 `GET/POST/PUT/PATCH/DELETE`，部署侧可收紧为只读或只允许部分方法。
- **重定向限制**：默认不跟随重定向；如果部署侧开启重定向，仍应按配置继续校验最终请求。
- **超时限制**：请求必须有超时，防止内部执行任务无限挂起。
- **真实报告**：默认不脱敏、不截断，报告面向内网执行复盘，需要看到登录账号、密码、token、Cookie、请求体和响应体；如部署侧有特殊合规要求，再显式打开可选脱敏或响应大小上限。
- **禁止 shell**：不执行 curl、bash、Python 片段，只执行结构化 HTTP plan。

开源使用时的建议是：本地和可信内网保持默认；如果把 Direct Run 暴露给更多调用方，再配置 allowlist、把 `CASE_FLOW_AIAPI_ALLOW_PRIVATE_NETWORKS=false`，并按需要收紧 `CASE_FLOW_AIAPI_ALLOWED_METHODS`。

## 与现有能力的关系

- `execution_target=api` 从“本地置 running”升级为真实执行。
- 覆盖泳道可保持不展示；如后续要展示，可新增单泳道 `api`，但不要影响 app/web 多端泳道。
- 诊断修复复用现有报告驱动链路：失败后读 AI API 报告，给出 case 修改建议，例如补 URL、补参数、补鉴权、改预期断言。
- 提交 bug 复用现有 bug 草稿链路：报告文本足够描述请求/响应失败，不需要截图。
- 助手模式后续应把 AI API 作为第三个内置工具接入。与标准/quick 的 case 执行不同，助手可优先复用 `POST /api/v1/aiapi/run` / `AIAPIKernel` 形态：用户说一句接口验证目标，助手整理为 `AIAPIDirectRunIn`，后台执行后把 HTML 报告作为附件，再生成自然语言 result 消息。

## 落地改动点

后端：

- [已实现] `services/ai_api/`：第一期内置 kernel，包含执行计划 schema、模型编译接口、静态编译器、plan 安全校验、HTTP runner、规则断言、HTML 报告生成和单元测试。
- [已实现] `AIAPIExecutionPlan.scenarios[]`：支持 CRUD、边界值、异常入参、等价类等语义化多变体执行。
- [已实现] scenario 断言与聚合：支持每个 scenario 独立断言、独立报告块、独立失败原因，最终按必需 scenario 聚合 item 状态。
- [已实现] `executions.submit_aiapi_execution`：标准工作流内部执行而非外部 HTTP submit。
- [已实现] `quick_executions.submit_aiapi_execution`：quick 工作流复用同一套 kernel 和报告格式，落 quick 专属 batch/item/work item。
- [已实现] 复用现有 submit DTO，先只使用 `case_ids/submission_name`，忽略设备池和缓存参数。
- [已实现] 标准路由 `POST /api/v1/executions/aiapi/submit`；quick 路由 `POST /api/v1/quick/executions/aiapi/submit`。
- [已实现] 对外 Direct Run 路由 `POST /api/v1/aiapi/run`：外部系统可只调用 AI API 能力，不需要创建标准 case 或 quick session。
- [已实现] 标准复用 `aiphone_execution_batches/items`，quick 复用 `quick_execution_batches/items`，均使用 `executor="ai_api"`、`platform="api"`。
- [已实现] AI API 报告写入 `/media/aiapi_reports/*.html`。
- [已实现] 设置项增加 allowlist、默认 base_url、默认 headers、超时、响应大小等。
- [规划] `case_repair.py` 报告读取先沿用 HTML reader；必要时再加 `ai_api` reader。

前端：

- [已实现] 标准首页执行队列里 `execution_target=api` 的子集调用 AI API submit，不再本地置 `running`。
- [已实现] 标准首页 API case 不展示设备/浏览器选择，只显示“AI API 将直接执行 HTTP 请求”。
- [已实现] 标准首页单条 API case 执行按钮直接提交，无设备弹窗。
- [已实现] quick 页面 API case 单条和批量均调用 quick AI API submit；不需要设备池。
- [已实现] 查看报告、诊断修复、提交 bug 沿用现有按钮语义。

文档：

- [已实现] 同步 `API契约.md`、`数据模型.md`、`产品方向、功能细节与流转总参考.md`。
- [已实现] AI API env 已同步 `开发与运维.md`。
- [已实现] 增加 E2E 脚本 `backend/scripts/e2e_ai_api.py`，覆盖标准模式与 quick 模式的批量自然语言 API case 执行。

## 端到端验证

可运行：

```bash
backend/.venv/bin/python backend/scripts/e2e_ai_api.py
```

脚本行为：

- 启动本地 mock API 服务，提供订单查询 `limit` 边界接口和用户 CRUD 接口。
- 启动本地 OpenAI Responses API 兼容假模型服务，让 `LLMPlanCompiler` 仍走 OpenAI SDK 的 `responses.create` 链路。
- 标准模式：直接写入一个一级目录 / 二级需求 / 两条 case 资产，一条自然语言描述“订单查询接口 limit 边界测试”，一条自然语言描述“用户接口增删改查测试”，然后一次调用 `POST /api/v1/executions/aiapi/submit` 批量提交两条 case。
- quick 模式：通过 `POST /api/v1/quick/sessions/import` 导入一份含两条 API case 的 Markdown，然后一次调用 `POST /api/v1/quick/executions/aiapi/submit` 批量提交两条 quick case。
- 分别等待标准 `case_work_items` 和 quick `quick_case_work_items` 回落为 `passed`，校验同一 submission 下各有 2 条 execution item，并校验 HTML 报告中包含关键 scenario 和真实响应证据。

## 待定项

- AI API 配置放全局 env，还是一级目录 functionMap，或两者结合。建议：执行范围和默认请求配置走 env/config，业务接口说明走 functionMap。
- [已实现] 多步骤请求链：scenario 内部支持 `steps[]` 顺序执行、`extract` 提取变量和 `{{变量名}}` 引用变量。
- 是否允许写操作接口。[已实现] 默认允许 `POST/PUT/PATCH/DELETE`，需要收紧时通过方法配置控制。
- 是否把 `aiphone_execution_*` 表改名为通用执行表。建议 MVP 不改名，避免迁移噪声。

## 场景化说明

例子 1：case 写“请求登录接口，手机号 13800000000，密码 test123，成功后返回 token”。一级目录 functionMap 里写了 `base_url=https://api.example.com`、登录路径 `/login`、请求字段名和成功响应示例。AI API 会让模型编译出 `POST https://api.example.com/login`，body 带手机号密码，断言状态码 200 且 `$.token` 存在。后端执行请求，断言通过则 case 通过；不通过则报告里列出实际响应和失败断言。

例子 2：case 只写“请求登录接口看看是否成功”，但没有域名、路径、参数、鉴权，也没有 functionMap。AI API 不会猜一个接口去打，也不会追问用户；它直接失败，报告写明“缺少 base_url、路径、参数和预期断言”，诊断修复建议用户把这些内容补进前置条件或 functionMap。

例子 3：case 写“完成查询接口 limit 参数边界测试，不能大于 1000”。functionMap 写清接口路径、请求体字段和错误格式。AI API 会展开为至少两个变体：`limit=1000` 应成功，`limit=1001` 应被拒绝。最终报告会分别记录两次请求和响应；如果 `1001` 返回参数错误，这个越界变体通过；如果 `1001` 也查成功了，这个越界变体失败，整个 case 失败。

例子 4：case 写“完成用户接口的增删改查测试”。AI API 会把它展开为创建用户、查询用户、修改用户、再次查询确认修改、删除用户、删除后查询确认不存在等场景。这里不是单纯看每次 HTTP 是否 2xx，而是看状态变化是否符合 CRUD 语义。

一句话：AI API 是“把自然语言 API case 编译成受控 HTTP 请求并执行”的内置执行器。它轻，但仍要有 batch、item、报告、失败诊断和执行边界，不能退化成随意执行 curl。
