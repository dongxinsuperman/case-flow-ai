# AI API 对外接口文档

状态：当前

本文面向外部调用方，描述如何只调用 Case Flow 的 **AI API 自然语言接口测试能力**。这个接口不要求调用方先在 Case Flow 里创建需求、case 或 quick session。

## 定位

AI API Direct Run 是一个同步执行接口：

```text
外部系统
  -> POST /api/v1/aiapi/run
  -> Case Flow 调模型编译自然语言 API 测试意图
  -> Case Flow 受控执行 HTTP 请求 / scenario 请求链
  -> Case Flow 生成 HTML 报告
  -> 响应返回结构化结果 + reportUrl + 可选 reportHtml
```

它复用标准/quick AI API 的同一套 kernel、计划 schema、安全校验、HTTP runner、断言和报告格式，但不写 `case_work_items`、不写 `quick_case_work_items`，也不创建执行批次。

注意：这个对外直跑接口是“单次同步执行”，没有 `items[]`。调用方只需要传一份已经合成好的 `function_map_context`。当前标准/quick 资产挂载流程同样只编译提交级顶层上下文，不会自动生成 item 级 Function Map。

## 接口

```http
POST /api/v1/aiapi/run
Content-Type: application/json
```

请求体使用后端字段名（snake_case）：

```json
{
  "title": "订单查询接口 limit 边界测试",
  "preconditions": "订单查询接口 limit 参数不能大于 1000。",
  "steps_text": "通过自然语言完成 limit 边界测试：请求 limit=1000 和 limit=1001。",
  "expected_result": "limit=1000 应成功；limit=1001 应被接口拒绝，返回 limit不能大于1000。",
  "function_map_context": "base_url=https://api.example.com\n订单查询接口 POST /orders/query，字段 limit 最大 1000。",
  "submission_name": "外部系统订单查询边界测试 2026-06-28 10:00:00",
  "return_report_html": true
}
```

字段说明：

| 字段 | 必填 | 说明 |
|---|---:|---|
| `title` | 否 | 测试标题；建议传，报告标题会使用它 |
| `preconditions` | 否 | 前置条件；可写账号、鉴权、业务前置数据 |
| `steps_text` | 否 | 自然语言操作步骤；可描述一次请求、CRUD、边界值、异常入参等测试意图 |
| `expected_result` | 否 | 自然语言预期；模型会据此生成断言和 scenario 预期 |
| `function_map_context` | 否 | API 上下文，只读执行参考；可补齐 base_url、path、method、字段、鉴权、测试数据、错误响应格式等，但不改变本次 `title/steps_text/expected_result` 的测试范围。Direct Run 只有这一份上下文，调用方如有“公共上下文 + 本次任务上下文”，应在调用前自行合并后传入。 |
| `submission_name` | 否 | 外部调用方自定义名称；当前只作为 direct run 的辅助标题，不落业务批次 |
| `return_report_html` | 否 | 默认 `true`；为 `false` 时响应只返回 `report_url`，不内联 HTML |

## 与批次 Function Map 的关系

当前标准模式和 quick 模式的资产挂载流程只编译提交级 `functionMapContext`。执行单元级 `items[].functionMapContext` 是预留扩展，不是当前自动发现结果。

`POST /api/v1/aiapi/run` 不走批次结构。它可以理解为“只执行一个 item”，请求体里的 `function_map_context` 就是本次执行的最终上下文；如果调用方有公共上下文和本次任务上下文，应先自行合并。

外部调用方如果要批量跑多条 API case，且每条 case 的 Function Map 不同，应按 case 分别调用 `/api/v1/aiapi/run`，或者在调用方侧先为每条 case 合并好上下文后再调用。不要把多条 case 的不同 Function Map 合成一份统一上下文传给同一次 direct run。

## 响应

```json
{
  "run_id": "direct-aiapi-0d6f...",
  "status": "success",
  "status_reason": "assertion_passed",
  "report_url": "http://127.0.0.1:8800/media/aiapi_reports/direct-aiapi-0d6f....html",
  "report_html": "<!doctype html>...",
  "result": {
    "status": "success",
    "statusReason": "assertion_passed",
    "plan": {
      "executable": true,
      "scenarios": []
    },
    "security": null,
    "exchange": null,
    "assertions": [],
    "scenarioResults": [],
    "error": "",
    "repairSuggestion": ""
  }
}
```

`status` 只有两类：

| status | 含义 |
|---|---|
| `success` | 所有必需断言 / scenario 均符合预期 |
| `failed` | 编译失败、安全拦截、请求异常或断言失败 |

常见 `status_reason`：

| status_reason | 含义 |
|---|---|
| `assertion_passed` | 执行成功且断言通过 |
| `compile_failed` | 自然语言或上下文缺少足够信息，模型判定不可执行 |
| `model_unavailable` | 模型配置或 SDK 不可用 |
| `model_failed` | 模型输出无法解析为合法执行计划 |
| `security_blocked` | 请求目标、方法或网络范围被安全配置拦截 |
| `request_error` | HTTP 请求过程异常 |
| `http_error` / `assertion_failed` | 断言失败 |

## curl 示例

```bash
curl -sS http://127.0.0.1:8800/api/v1/aiapi/run \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "订单查询接口 limit 边界测试",
    "steps_text": "请求 limit=1000 和 limit=1001，测试超过边界值是否被拒绝。",
    "expected_result": "limit=1000 应成功；limit=1001 应返回 limit不能大于1000。",
    "function_map_context": "base_url=https://api.example.com\nPOST /orders/query body: {limit:number}",
    "return_report_html": false
  }'
```

## 语义化执行说明

外部调用方不需要把自然语言转换成 curl。它只要把测试意图写清楚：

- “完成用户接口的增删改查测试”会被模型展开成创建、查询、修改、复查、删除、删除后查询等 scenario。
- “limit 不能大于 1000，测试超过边界值”会被模型展开成 `limit=1000` 和 `limit=1001` 等边界 scenario。
- 对 `expected_outcome=rejected` 的 scenario，被接口正确拒绝才是通过；例如 `limit=1001` 返回 400 和错误文案时，该 scenario 通过。

## 配置说明

AI API 对外直跑接口仍受全局配置约束。变量名必须保持英文，因为它们是环境变量；含义如下：

| 环境变量 | 默认值 | 中文说明 |
|---|---:|---|
| `CASE_FLOW_AIAPI_ALLOWED_HOSTS` | 空 | 允许访问的域名或 IP 白名单。默认空表示不限制；要收紧访问范围时再填写，多个值用英文逗号分隔。 |
| `CASE_FLOW_AIAPI_ALLOWED_BASE_URLS` | 空 | 允许访问的接口基础地址白名单。默认空表示不限制；适合只允许访问某几个服务前缀。 |
| `CASE_FLOW_AIAPI_DEFAULT_BASE_URL` | 空 | 当模型只编排出 `/path`，没有完整域名时，用这个基础地址拼成完整请求地址。 |
| `CASE_FLOW_AIAPI_DEFAULT_HEADERS` | 空 | 默认请求头，JSON 字符串格式；例如统一放鉴权 token、租户标识。报告会完整展示。 |
| `CASE_FLOW_AIAPI_ALLOWED_METHODS` | `GET,POST,PUT,PATCH,DELETE` | 允许执行的 HTTP 方法。要禁止删除或写操作时，在这里缩小范围。 |
| `CASE_FLOW_AIAPI_ALLOW_PRIVATE_NETWORKS` | `true` | 是否允许请求本机、localhost、127.0.0.1、私网和内网地址。默认允许；要收紧时改成 `false`。 |
| `CASE_FLOW_AIAPI_MAX_TIMEOUT_SECONDS` | `20` | 单个接口请求最大超时时间，单位秒。 |
| `CASE_FLOW_AIAPI_MAX_RESPONSE_BYTES` | `0` | 响应体读取上限。`0` 表示完整保留响应，不截断。 |
| `CASE_FLOW_AIAPI_FOLLOW_REDIRECTS` | `false` | 是否跟随 HTTP 重定向。默认不跟随。 |

默认允许请求 HTTP/HTTPS 的本机、私网和内网服务，便于直接测试内部接口。安全收紧能力保留但默认关闭：白名单为空时不限制访问地址；只有部署侧显式配置白名单时才按白名单收紧访问范围；需要禁止本机/私网访问时配置 `CASE_FLOW_AIAPI_ALLOW_PRIVATE_NETWORKS=false`。报告默认完整保留请求、响应、请求头、请求体，不做脱敏。

本仓库当前无鉴权；如果这个接口暴露给其它内部系统，建议在网关或企业版层面加调用方鉴权、审计和配额。

## 场景化说明

外部系统要测试“订单查询接口 limit 超过 1000 必须失败”，只需要传自然语言标题、步骤、预期和 functionMap。Case Flow 会让模型生成两个请求：`limit=1000` 和 `limit=1001`。如果 `1001` 被拒绝，报告里会显示这个越界 scenario 通过；如果 `1001` 也成功返回数据，报告里会显示该 scenario 失败，外部系统可直接读取 `status=failed` 和 `report_url`。
