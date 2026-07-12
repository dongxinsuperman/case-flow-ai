# Markdown 导入与层级配置

Case Flow 的 Markdown 导入层级不是固定写死的。标准模式提供部署级公开配置；快速模式按文件结构动态保留路径层级。

## 两种模式

| 模式 | 层级来源 | 当前规则 |
|---|---|---|
| 标准模式 | `backend/config/markdown_import.json` | 部署方显式定义总层级数、路径层级名称和核心字段名称 |
| 快速模式 | 当前导入的 Markdown | 最后四级固定解释为标题、前置、步骤、预期；此前的全部层级动态保存为路径 |

两种模式都要求一条 Case 至少具有：测试集 + 最后四个核心字段，因此完整分支不能少于 5 级。

## 标准模式配置

默认配置位于：

```text
backend/config/markdown_import.json
```

也可以通过环境变量指定另一份配置：

```env
CASE_FLOW_MARKDOWN_IMPORT_CONFIG_PATH=config/markdown_import.json
```

配置在后端进程中缓存，修改后需要重启后端。

当前继承行为：如果配置路径不存在，系统会使用代码内置的默认 8 级配置；如果文件存在但内容不合法，则明确报错。路径写错也会触发默认配置，因此部署后应通过一次实际导入确认自定义层级已经生效，不能仅凭服务成功启动判断配置已加载。

默认配置是 8 级：

```json
{
  "levels": [
    { "index": 1, "role": "suite", "displayLabel": "测试集" },
    { "index": 2, "role": "path", "displayLabel": "模块" },
    { "index": 3, "role": "path", "displayLabel": "功能点" },
    { "index": 4, "role": "path", "displayLabel": "测试功能点" },
    { "index": 5, "role": "case_title", "displayLabel": "测试标题" },
    { "index": 6, "role": "preconditions", "displayLabel": "前置条件" },
    { "index": 7, "role": "steps", "displayLabel": "操作步骤" },
    { "index": 8, "role": "expected", "displayLabel": "预期结果" }
  ],
  "trimSeparators": ["：", ":"]
}
```

## 可调整范围

- `levels` 最少 5 项，可以是 5、6、8 级或更多。
- Level 1 的 `role` 必须是 `suite`。
- Level 2 到最后四级之前，全部使用 `path`；这些路径层级可以增加、减少或改 `displayLabel`。
- 最后四级的 `role` 必须依次是 `case_title`、`preconditions`、`steps`、`expected`。
- 最后四级的 `displayLabel` 可以改成团队自己的叫法，例如“用例名、前提、步骤、断言”。
- `trimSeparators` 定义从核心字段正文前移除的分隔符，默认同时支持中文和英文冒号。
- `index` 必须从 1 开始连续递增，并与数组位置一致。

例如，改成 6 级：

```json
{
  "levels": [
    { "index": 1, "role": "suite", "displayLabel": "测试集合" },
    { "index": 2, "role": "path", "displayLabel": "业务线" },
    { "index": 3, "role": "case_title", "displayLabel": "用例名" },
    { "index": 4, "role": "preconditions", "displayLabel": "前提" },
    { "index": 5, "role": "steps", "displayLabel": "步骤" },
    { "index": 6, "role": "expected", "displayLabel": "断言" }
  ],
  "trimSeparators": ["：", ":"]
}
```

对应 Markdown：

```markdown
- 登录测试
  - 账号体系
    - 用例名：手机号验证码登录成功
      - 前提：用户已注册
        - 步骤：打开 App、输入手机号和验证码
          - 断言：进入首页
```

## 快速模式

快速模式不读取部署级层级配置。它按每条完整分支的位置解析：

```text
第一级          → 测试集
中间任意层级    → 路径节点
倒数第四级      → 测试标题
倒数第三级      → 前置条件
倒数第二级      → 操作步骤
最后一级        → 预期结果
```

因此，快速模式可以导入“测试集 + 核心四级”的最小 5 级结构，也可以在中间增加业务线、模块、功能点等任意数量的路径层级。路径会原样保存并在脑图、列表和导出结果中继续使用。

## 标题完整性

测试标题开头的 `【标签】` 可以复制识别为结构化标签，但不会从标题正文中删除。例如 `【改造】【重点】逐层返回` 在脑图、列表、编辑、碰撞比较、执行、诊断、Bug 和 Markdown 导出中始终保留完整文本；标签只作为额外元数据使用。

`raw_title` 是完整标题事实源，`clean_title` 仅作为兼容镜像。新增或编辑标题必须提交完整 `rawTitle`；导出或执行遇到缺失完整标题的异常数据时明确失败，不回退到可能已经丢失标签的标题。

## 错误处理

- 标准模式配置结构不合法时明确报错，不猜测层级含义。
- Case 分支缺少中间父级或最后四个核心字段时，本次导入整体失败，不半成功入库。
- 同一配置下，标准模式要求每条完整 Case 分支符合配置声明的层级数。
- 快速模式允许不同导入文件使用不同深度，但同一条 Case 分支仍必须完整。

标准模式配置是公开产品行为，应随源码维护或由部署方显式提供；真实 Case 数据和企业模板内容不应写入配置文件。
