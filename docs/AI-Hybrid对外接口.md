# AI Hybrid 对外接口

AI Hybrid 是 Case Flow 内置的异步混合执行器。它通过与 AI Phone / AI Web 同构的提交协议受理 case，并在本进程内编排当前运行时真实注册的 AI API、AI Web、AI Phone 和扩展工具。

当前基址由 `CASE_FLOW_AIHYBRID_BASE_URL` 指定；本地同进程默认是 `http://127.0.0.1:8800/aihybrid`。以下路径均相对该基址。当前按内网服务处理，不带鉴权。

## 停止 Hybrid 编排

```http
POST /api/submissions/{submission_id}/cancel
Content-Type: application/json
```

请求体：

```json
{
  "caseIds": ["cf-101", "cf-102"]
}
```

- `caseIds` 指定时，只停止这些 Hybrid 执行单元。
- 省略 `caseIds`，或传空数组时，停止该 submission 的全部执行单元。
- `caseIds` 不属于该 submission 时，返回 `400`；submission 不存在或已结束时返回 `404`。

响应只表示 Hybrid 已接受并执行本地停止，不是对子服务终态的确认：

```json
{
  "submissionId": "aihybrid-xxx",
  "acceptedCaseIds": ["cf-101", "cf-102"]
}
```

### 停止语义

取消只作用于 **Hybrid 自身的编排任务**：

- 立即取消当前 Hybrid coroutine，并清理 Hybrid 自己创建的本地等待。
- 不再发起后续工具调用。
- 已经提交给 AI Phone / AI Web 的子任务不发取消请求、不轮询、不等待，也不维护“是否真的停止”的子任务状态。
- 子任务在 Hybrid 停止后才发来的回调会被忽略；停止单元不写报告，也不发送自己的 item 完成/失败回调。
- 若只停止 submission 内的一部分 case，其余 case 仍按原流程收尾，随后仍可发送该 submission 的父终态；整批停止时不发送父终态。
- `GET /api/submissions/{submission_id}` 中的 `stopped` 只说明 Hybrid 编排已停止，**不表示**已下发的 Phone/Web 子任务停止。

部署方自定义工具是否能撤销自己的远端工作，由该工具自己的协议决定；Case Flow Core 不内置企业 CLI，也不会为不具备撤销协议的工具猜测结果。

这不是兜底或状态猜测：Hybrid 不会把子服务继续运行误标为成功、失败或已停止。可见影响是，已经派出的子服务可能继续执行，但其后续状态不再进入 Hybrid；这是该接口的刻意边界。

### 场景例子

一个 Hybrid case 先把步骤 A 发给 AI Phone，接着原计划要调用 AI Web 校验结果。此时收到取消：Hybrid 立即结束等待，步骤 B 不会再发起；步骤 A 不会被撤销，也不会再由 Hybrid 等待或汇总。Hybrid 不产出这条 case 的报告。

## 相关接口

- `POST /api/submissions`：异步受理 Hybrid submission，立即返回 submission ID。
- `GET /api/submissions/{submission_id}`：读取 Hybrid 自身的内存态；服务重启后该状态不可恢复。

完整提交字段和父/子回调协议见 [API 契约](API契约.md) 与 [Hybrid 混合执行器方案](Hybrid混合执行器方案.md)。
