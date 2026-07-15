# AI Hybrid 混合执行器方案

## 定位

AI Hybrid 是与 AI Phone、AI Web、AI API 平级的执行器。它接收一条混合目标，使用豆包模型按 ReAct 循环选择已注册工具，最后输出任务级结论、证据和报告。

AI Hybrid 不假设任何公司内部系统存在。可用工具以本次运行时注册表为唯一事实源。

## 官方工具

- `ai_phone`：调用公开的 AI Phone 服务执行 App 任务。
- `ai_web`：调用公开的 AI Web 服务执行 Web 任务。
- `ai_api`：使用 Case Flow 内置 AI API 内核执行 HTTP 场景。
- `report_reader`：按索引读取子报告文字和截图证据。
- `function_map`：按需读取本次挂载的 Function Map，用于账号、角色与设备的绑定判断。

CLI 只是一种可选 Tool，不是 Hybrid 的固定能力。开源版本不内置公司的 CLI Tool，也不提供测试账号或业务数据准备能力。Case 依赖这类前置且现有工具无法完成时，Hybrid 必须返回 `needs_human`，明确说明需要部署方二次开发或先准备数据；没有 CLI Tool 不影响其他工具运行。

## 运行规则

1. 模型只能调用本轮注册表中真实存在的工具。
2. 数据前置需要扩展工具但未注册时，必须返回 `needs_human` 并说明缺少能力。
3. 不允许跳过前置后继续执行，也不允许把工具缺失解释为成功。
4. 子工具结果只是 observation，最终状态由 Hybrid 对照 Case 预期收敛。
5. LLM 不可用、工具未注册、报告证据不足或达到步数/时长上限时，必须保留已获得证据并明确失败或转人工。

## Function Map

当前资产挂载流程编译提交级 `functionMapContext`，Hybrid 在调用 AI Phone、AI Web 或 AI API 子工具时原样透传。标准模式和快速模式还会把同一批已筛选资产作为结构化 `functionMaps` 一并交给 Hybrid；模型首轮只看到其中的目录（资产 ID、标题、简介和适用端），正文只在 Hybrid 明确调用 `function_map` 后读取。

结构化目录只服务于资源绑定，不能替执行器决定页面操作。例如，Map 明确「教师账号使用 `teacher-device`」且当前步骤需要该账号时，Hybrid 会在调用 AI Phone 时指定该 alias。此时系统只会校验和等待这台设备：设备离线、别名多义、平台冲突或等待超时都会返回 `needs_human`，**绝不会换另一台设备继续执行**。没有明确绑定时，才保留 AI Phone 原有的按平台资源池调度。

兼容外部旧调用只传 `functionMapContext` 的情况：系统会尝试解析其中的资产边界；无法识别边界时将整段保留为一个 Map，而不是静默丢弃内容。这不是自动发现或业务规则兜底。不存在隐式全局 Function Map 兜底。

## 回调与报告

- 父回调：Hybrid 完成 item 或 submission 后回调 Case Flow 主执行链。
- 子回调：AI Phone / AI Web 子任务完成后唤醒当前 Hybrid 工具调用。
- 父状态只能由父回调推进，子回调不能直接修改 Case 主状态。
- 失败报告必须保留真实子报告证据；解析不到证据时不得猜测截图或生成修复结论。

## 取消边界

`POST /aihybrid/api/submissions/{submission_id}/cancel` 支持停止 Hybrid 自身编排；可传 `caseIds[]` 选择执行单元，空数组表示整批。停止后不再调用后续工具，并结束 Hybrid 当前的本地等待。已经下发的 AI Phone / AI Web 子任务不取消、不查询、不等待，迟到子回调忽略。详细请求/响应见 [AI Hybrid 对外接口](AI-Hybrid对外接口.md)。

## 扩展边界

其他模型和执行器由使用方自行实现。部署方也可以按自己的需要增加工具，包括采用 CLI 形态的工具。仅暴露 OpenAI 风格接口不代表模型兼容；除豆包/Ark 外的模型由使用方自行修改和验证。
