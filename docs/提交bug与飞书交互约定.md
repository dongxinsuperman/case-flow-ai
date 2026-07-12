# 提交 bug 到飞书项目 · 实现与交互约定（SOP）

> 本文沉淀"提交 bug"功能的设计、换空间接入步骤，以及逆向飞书 OpenAPI 踩出来的关键约定。改这块前务必先读，避免重复踩坑。

## 一、整体设计

- **Meta-first 表单事实源**：提交 bug 弹窗不以历史 bug 样例或本地硬编码字段为事实源。运行时拉 `GET /open_api/{project}/work_item/{type}/meta`，由飞书返回当前空间、当前工作项类型的建单字段（字段名、顺序、必填、类型、枚举、默认值），弹窗按这份 meta 生成。
- **模板由配置确定**（`backend/config/feishu_issue.json`，已 gitignore，模板见 `.example.json`）：每空间只配 `work_item_type + template_id + 标题/描述/附件字段 + 关联需求字段 + 少量字段数据源规则`。`template_id` 是“这张 bug 表单是哪一种缺陷”的锚点；字段清单和枚举不在配置里复制。
- **字段填值分层**：meta 决定“有哪些字段”，系统再按优先级给字段填默认值。系统确定不了的字段交给模型按字段名/options/case/诊断/需求信息预填；模型只是默认值，用户可二次编辑。
- **后台预生成**：失败 case 诊断完后台 `precompute_bug_draft` 把标题润色+字段预填存进 `case_bug_drafts`，点"提交 bug"秒开。草稿不绑定具体登录人，提交人/报告人这类动态字段只显示占位。
- **两段式提交**（关键）：
  1. `create` 同步建单（标题、描述、人员、关联——这些建单即落库），**立即返回 bug 链接**；
  2. 后台 `finalize_bug`：**等模板默认稳定（默认 12s，`bug_field_settle_seconds`）**→ 回写单选/多选 → 通过 MCP 第二通道把截图渲染进描述。不阻塞用户。

### 字段填值优先级

每一个飞书 meta 返回的字段，都按同一套顺序处理，不给单空间写特殊逻辑：

1. **字段语义识别**：先把字段归成 `submitter`、`assignee`、`watcher`、`sprint`、`linked_requirement`、普通枚举/文本等语义。识别优先级是显式配置 `semantic` > 现有 `source` > 字段类型 + 通用命名（如“报告/提交/创建”归 submitter，“经办/负责/参与/开发”归 assignee，“迭代/排期”归 sprint）。这一步不按空间写死。
2. **系统权威字段**：当前 case 所属需求、标题、描述、提交人、诊断截图/报告链接等。只要语义命中，就必须填；不能因为字段已在 meta 中出现而跳过。
3. **需求快照字段**：从 `requirement_pool.source_payload` 搬运当前需求信息，例如 `planning_sprint`、需求角色人员、需求字段映射。
4. **空间固定规则**：`feishu_issue.json` 中显式配置的 `fixed`、`current_month`、`current_month_zh`、`requirement_field_mapped` 等。
5. **模型预填字段**：`source=model` 的字段，以及没有系统来源、没有飞书默认值的必填下拉/多选/文本字段。模型必须从飞书 options 里选，不能自造枚举。
6. **飞书默认值**：meta 里有默认值时沿用。
7. **人工填写**：仍然为空的可编辑字段展示给用户处理；选填空缺可以留空。

字段语义对应的默认填值规则：

- `submitter`：当前提交人。字段在不同空间可以叫报告人、提交人、创建人、发现人等；弹窗统一显示 `右上角当前用户`，不缓存具体人名、不铺全员候选，真正提交时再用右上角所选用户的 `feishu_user_key` 写入飞书。
- `assignee`：经办/处理/开发/参与类人员。默认从当前需求看板里的开发角色搬运，可按需求快照候选编辑。
- `watcher`：关注/通知类人员。默认不铺全员，必填时优先当前提交人。
- `sprint`：规划迭代/所属迭代/排期。先从当前需求看板的已选值搬运，保留真实 ID，并尽量通过飞书 sprint query 解析名称；解析失败才降级显示 ID。
- `linked_requirement`：关联需求/迭代需求。默认填当前 case 所属需求。

这套优先级的核心是：飞书 meta 只决定表单结构，不决定业务值；业务值由系统、需求快照、配置、模型和人工依次补齐。

### 运行时数据来源

运行时不依赖某条历史 bug 样例。样例 bug 只用于人工确认字段语义。

```text
case / diagnosis
  -> 标题、描述、失败原因、步骤、预期、报告、截图

case -> requirement_item -> requirement_pool
  -> source_space、当前需求 id、需求快照、QA/开发、规划迭代

feishu_issue.json
  -> 目标空间、issue 类型、template_id、关联需求字段、少量不可推断规则

GET /open_api/{project}/work_item/{type}/meta
  -> 当前飞书建单表单字段、必填、枚举、默认值

模型
  -> 给不确定字段生成默认值
```

因此，飞书后台新增一个字段时，只要它出现在 meta 里，弹窗就应该能展示；如果它是未知必填字段，系统应尽量让模型先填一版，用户最终可改。

### 当前实现边界与后续增强点

当前实现已经具备 meta-first 的基础，并已覆盖关联需求、提交人动态占位、需求角色、迭代搬运、枚举/文本模型预填等通用路径。后续如果继续贴近飞书原生体验，优先处理：

- **前端使用系统标准字段模板**：当前支持 select、multi_select、text/multi_text、user/multi_user 搜索 chips、关联工作项/迭代已选项展示与手动补 ID。它不是飞书原生前端，但字段类型、枚举、必填和默认值来自飞书 meta。若要进一步贴近飞书，需要补远程人员搜索、迭代搜索、关联工作项搜索。
- **候选项远程搜索**：当前弹窗优先展示已经能从需求快照、当前用户、飞书 meta 拿到的候选；飞书原生那种全量人员/工作项远程搜索还没有接。

## 二、飞书 OpenAPI 关键约定（踩坑记录，务必遵守）

| 事项 | 结论 |
|---|---|
| 建单 | `POST /open_api/{project}/work_item/create`，body `{work_item_type_key, template_id, name, field_value_pairs}`，返回新 id |
| 更新 | `PUT /open_api/{project}/work_item/{type}/{id}`，body `{update_fields:[{field_key, field_value}]}` |
| 删除 | `DELETE /open_api/{project}/work_item/{type}/{id}` |
| 建单元信息 | `GET /open_api/{project}/work_item/{type}/meta`（字段/顺序/必填 `is_required==1`/可见 `is_visibility==1`/选项/默认值一站式） |
| 文件上传 | `POST /open_api/{project}/file/upload`（multipart `file`），返回下载 URL |
| **单选 select** | `field_value` = `{"value": option_id}`。**不是** `{"option_id":...}`（那个不报错但不落库！） |
| **多选 multi_select** | `field_value` = `[{"value": id}, ...]` |
| 多用户 multi_user | `[user_key, ...]` |
| 关联工作项 | 单选=`id`(int)；多选=`[id,...]` |
| **select/多选落库时机** | 建单时设的单选/多选会被**模板默认值异步覆盖**！必须"建单 → 等模板稳定（~10s+）→ 再 update"。文本/人员/关联建单即落库，不受影响。 |
| **富文本图片渲染** | 首次建单和字段仍走公共 OpenAPI；公共 OpenAPI 的 update 不把 markdown 图片转成图片块，所以截图渲染改走 MCP 第二通道：`upload_file(resource_type=16)` + 签名上传 + `update_field(description=完整描述 + ![](file_url)<!--file_token-->)`。失败只写后台日志，不退回链接兜底。 |
| 单空间无权限 | `10301 Check Token Perm Failed`。拉取已做成"单空间失败跳过、不中断整体"。 |

## 三、换一个新空间接入 bug 提交（SOP）

> 代码零改动，只加配置。前提：该空间已在 `feishu_project.json` 配了 `role_map`（拉需求时已配）。

1. 用飞书 MCP 查该空间 issue 字段：
   - `get_workitem_field_meta(project_key, "issue")` → 看字段 key / 顺序 / 必填 / 模板默认；
   - `list_workitem_field_config(project_key, "issue", field_keys=[...])` → 看 select 的 `option_id`、字段中文名。
2. 在 `backend/config/feishu_issue.json` 的 `spaces[]` 加一段：
   - `project_key / template_id`（meta 里 `template` 的 option_id）；
   - `title_field`(一般 `name`) / `description_field`(一般 `description`) / `attachment_field`(`multi_attachment`)；
   - `link_requirement_field`（关联需求字段 key，如 `_field_linked_story`）；
   - `field_sources`：把我们的值映射到该空间字段 key。可用 `source`：
     - `model`（模型按该字段 options 挑，如优先级/标签/业务归属）
     - `current_user`（报告人=当前用户）
     - `requirement_roles` + `roles`(+`fallback_roles`)（经办人=前后端，没开发回退测试）
     - `requirement_field` + `from`（搬需求字段，如迭代）
     - `requirement_field_mapped` + `from` + `map`（需求某选项 → 本字段选项，如小组→缺陷分组）
     - `case_expected` / `diagnosis_reason`（case 预期 / 诊断原因，仅当要单独填字段时；默认我们只写进描述）
     - `fixed` + `value`（固定 option_id，如发现阶段/发现方式）
3. 没配的字段：自动用飞书默认 / 必填空缺交给模型分析 / 选填留空，无需逐个配。模型未给出有效选项时不自动选第一个。

## 四、需求拉取语义（重要）

正确模型：**飞书工作项 = 二级需求，实时同步**。重拉 = 没有则新建、相同则更新、拉不到但已有则保持现状（不删）。

- **新建/更新/保持现状**（`pull_feishu_project`）：按 `external_key` upsert 需求池；本次没拉回来的（关闭/删除/超出 `created_after`）原样保留——这是特性（不能把你正在测的需求弄没）。
- **已挂二级需求实时回灌**：被拉回且仍有 QA 的需求，会把已挂在它上面的二级需求**同步状态、标题、负责人(QA)**：case 完全不动（只是挂着）；version 不动（人工维护）；负责人按飞书当前 QA 全量对齐（新增缺的、移除已不在的，即"换了测试就转移负责人"）。
- **多 QA**：拉取收下该需求全部 QA；首次建二级需求 / 之后每次重拉都让每个 QA 各有一条 `RequirementAssignee`，几个测试人首页都能看到、按测试集软分工。
- 注意：飞书里 QA 全清空的需求，拉取阶段会被 `skipped_no_qa` 跳过（不更新）→ 这种保持现状。

## 五、回归测试

- `backend/tests/test_bug_submit.py`：`_format_value`（单选 {value}/多选/多用户/关联/文本）、`_pairs_from_fields`（装配 + 建单/后写拆分 + 只读用 submit_value + 提交人动态注入 + 空值跳过）、`_normalize_meta`、MCP 富文本图片描述拼接。
- `backend/tests/test_feishu_project_mcp.py`：MCP `tools/call` 解析、富文本图片签名上传、错误识别。
- `backend/tests/test_workbench_projection.py`：`_display_numbers`（单集纯数字 / 多集分段）。
