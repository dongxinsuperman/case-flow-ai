# Function Map 执行上下文协议

状态：当前公开协议  
范围：Case Flow 向 AI Phone、AI Web、AI API 和 AI Hybrid 提交 Function Map 的当前约定。

资产如何创建和挂载，见 [Function Map](Function-Map.md)。本文只说明当前代码实际发送什么，以及执行器应该如何消费和保留证据。

## 当前结论

当前实现使用一份**提交级上下文**：

```text
payload.functionMapContext
```

它对本次提交下的所有 item 生效。当前版本不会根据单条 Case 自动发现 Function Map，也不会自动生成 `items[].functionMapContext`。

一个实际提交形态：

```json
{
  "submissionName": "批量执行",
  "functionMapContext": "本次提交编译出的 Function Map...",
  "items": [
    {
      "caseId": "cf-1",
      "runContent": "..."
    },
    {
      "caseId": "cf-2",
      "runContent": "..."
    }
  ]
}
```

没有匹配的显式挂载时，`functionMapContext` 字段直接省略；不回落全局配置，也不生成猜测内容。

## Case Flow 如何编译

### 标准模式

Case Flow 根据本次 Case 关联的二级需求，读取：

```text
二级需求本级显式挂载
+ 所属一级目录显式挂载
```

然后执行：

1. 按执行器适用端过滤。
2. 按资产 ID 去重。
3. 一级目录和二级需求重复时保留一级目录继承来源。
4. 按资产 ID 稳定排序。
5. 给每份资产增加标题、资产 ID 和来源边界后拼接。

### 快速模式

快速模式只读取当前 Session 从资产库显式选择的卡片，再使用相同的适用端过滤、去重和拼接规则。

## 适用端

| 执行器 | 编译范围 |
|---|---|
| AI Phone | `App` 卡片 |
| AI Web | `Web` 卡片 |
| AI API | `API` 卡片 |
| AI Hybrid | `App`、`Web`、`API` 卡片 |

AI Hybrid 收到当前有效的 `functionMapContext` 后，在调用 AI Phone、AI Web 或 AI API 子工具时继续透传同一份上下文。Hybrid 不重新发现、不拆分，也不裁剪 Function Map。

## 资产边界格式

每份资产在合成时保留清晰边界：

```text
--- FUNCTION MAP: 登录规则 ---
资产 ID: 12
来源: 一级目录显式挂载

正文……
```

执行器不能静默删除边界或截断正文。如果无法接收完整上下文，应明确失败并返回可排查错误。

## `items[].functionMapContext` 的定位

执行单元级字段是协议的预留扩展，不是当前版本的自动发现结果。

部分执行路径已经能够在收到该字段时，把它与顶层上下文合成为当前 item 的 `effectiveFunctionMapContext`；但当前资产库与挂载流程只编译顶层 `functionMapContext`。二次开发方不能假设系统会自动为每条 Case 生成单独上下文。

如果未来启用执行单元级上下文，合成规则应为：

```text
effectiveFunctionMapContext =
  payload.functionMapContext
  + items[n].functionMapContext
```

该扩展必须先补齐发现准确性、执行快照和公开验证，再标记为当前能力。

## 执行证据

Case Flow 的执行流水至少要能够定位：

- 本次提交的顶层 `functionMapContext`。
- 执行模式、执行器和关联对象。
- 编译或提交状态与失败原因。
- Hybrid 向子工具透传的上下文。

排查时应明确区分：

- 没有显式挂载。
- 挂载资产与执行端不匹配。
- Case Flow 编译或提交失败。
- Hybrid 没有透传。
- 子执行器没有消费或拒绝了上下文。

## 简化理解

Function Map 像执行前准备的资料袋。当前由人决定把哪些资料挂到某个目录、需求或快速会话，Case Flow 再按执行端整理成一份资料袋交给执行器。系统不会自己去全库猜资料；交出去的内容可以从执行流水里核对。
