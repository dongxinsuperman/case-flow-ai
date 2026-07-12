# AGENTS.md — Case Flow 协作指南（人和 AI 都先读这份）

这是仓库的入口说明。任何人或 AI 接手本项目，先读这一页，再按需翻 `docs/`。

## 这是什么

Case Flow 是一个**开源的 AI 辅助测试工作台**：Markdown 测试用例导入 → 打磨阶段碰撞审批 → 结构化用例可视化（脑图）→ 分发到执行器（当前主要是 AI Phone）→ 接收执行反馈 → 基于报告给修复建议。

本仓库是 Case Flow 的**开源版本**，不含私有令牌、私有 CI、私有执行器地址或私有报告格式。企业专属能力应放在独立私有项目中。

## 仓库结构

```text
backend/     FastAPI 应用、领域服务、SQLAlchemy 模型、Alembic 迁移、测试
web/         Vue 3 + Vite + TS + Pinia 前端
docs/        产品方向、架构、数据模型、API、集成、运维等文档
docker/      容器文件
AGENTS.md    本文件
```

后端分层（`backend/app/`）：`api/`(路由) · `schemas/`(DTO) · `models/`(ORM) · `repositories/`(数据访问) · `services/`(领域编排) · `executors/`(执行器接口) · `report_readers/`(报告读取) · `llm/`(模型相关公共定义) · `core/`(配置/DB/设置)。

前端分层（`web/src/`）：`api/`(类型化客户端) · `stores/`(Pinia 业务状态) · `graph/`(脑图适配) · `components/` · `pages/`(路由页) · `types/` · `styles/`。

主要页面：
- 项目页 `/requirements`：外部项目池 → 纳入一级目录 → 形成二级需求；一级目录承载 functionMap。
- Case 页 `/case-assets`：选二级需求 → Markdown 导入 → 打磨碰撞审批 → 资产增删改查。
- 首页 `/`：执行工作台——概览、任务切换、状态脑图、Case 操作、单条/批量执行、停止、报告、错误修复。
- 快速模式 `/quick`：Markdown 直接导入、编辑、执行和导出；基础链路不依赖飞书项目或任何企业自定义 Tool。

## 怎么跑（本地）

| 服务 | 地址 | 启动 |
|---|---|---|
| 后端 | http://127.0.0.1:8800 | `cd backend && .venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8800` |
| 前端 | http://127.0.0.1:5173 | `cd web && npm run dev` |
| AI Phone（外部依赖，本仓库不含） | http://127.0.0.1:8000 | 由 AI Phone 项目自行启动 |

- 前端 Vite 把 `/api` 代理到后端 8800。
- 健康检查：`GET /api/v1/healthz`。
- 迁移：`cd backend && .venv/bin/alembic upgrade head`。
- 测试：`cd backend && .venv/bin/pytest -q`；前端构建校验：`cd web && npm run build`。
- 配置走 env，见 `backend/.env`（前缀 `CASE_FLOW_`）。详见 `docs/开发与运维.md`。

## 必须知道的关键约束（改代码前先记住）

1. **业务状态是唯一事实源；脑图只是投影。** 不在脑图节点里维护第二套状态（见 `docs/架构说明.md`）。
2. **前后端字段命名自动转换**：后端纯 snake_case，前端纯 camelCase；`web/src/api/client.ts` 出站 `toSnake`、入站 `toCamel`。写代码各用各的，别手动转。
3. **执行状态“最后一次覆盖前一次”**：case 执行状态只由回调或手动改推进；**不做主动轮询/对账**（这是有意决策，别加 reconcile）。
4. **打磨碰撞是覆盖式**：每个二级需求只保留一份当前工作集；碰撞一对一匹配、必须整体提交（见 `docs/产品范围.md`）。
5. **AI Phone 内网无鉴权**：提交不带 token（已确认）。地址走 env（`CASE_FLOW_AIPHONE_BASE_URL` / `CASE_FLOW_PUBLIC_BASE_URL`）。
6. **执行器按真实协议接入**：当前公开维护 AI Phone、AI Web、AI API、AI Hybrid。不得把一个执行器的可用性当作另一个执行器成功的依据。
7. **改 DB 必须走 Alembic 迁移**，不要手改表。
8. **Function Map 使用资产 + 挂载模型**：一级目录、二级需求和 Quick Session 挂载资产；执行时按当前容器和执行端编译，不回落到隐藏全局上下文。
9. **任何功能上线前必须做兜底审查**：检查是否新增默认猜测、静默降级、吞异常、硬编码小上限或“失败也继续产出”的逻辑。准确性、证据链和可排查性优先；只有明确属于业务必需且不会掩盖问题的降级，才允许保留并说明原因。

## 工作习惯

- 改完后端跑 `pytest`，改完前端跑 `npm run build`，都要绿。
- 公开产品行为以 `docs/功能说明.md` 及对应集成文档为准；新增/改行为要同步更新公开文档。
- 公开文档不得依赖本地研发草稿、真实环境记录或敏感配置。
- 文档导航见 `docs/索引.md`。

## 文档去哪找（速查）

| 我想了解… | 看这份 |
|---|---|
| 全部文档导航 | `docs/索引.md` |
| 产品行为、功能点和依赖矩阵 | `docs/功能说明.md` |
| 快速模式最小配置与边界 | `docs/快速模式.md` |
| Markdown 导入层级配置 | `docs/Markdown层级配置.md` |
| 分层原则、开源/企业边界 | `docs/架构说明.md` |
| 范围、打磨碰撞决策 | `docs/产品范围.md` |
| 数据表与关系 | `docs/数据模型.md` |
| 后端接口清单 | `docs/API契约.md` |
| AI Phone 提交/回调/设备/functionMap | `docs/AI-Phone集成.md` |
| 本地启动、端口、env、迁移 | `docs/开发与运维.md` |
| Function Map 资产和执行上下文 | `docs/Function-Map.md` |
| 模型配置与维护边界 | `docs/模型配置.md` |
