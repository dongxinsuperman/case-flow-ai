from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class UserOut(BaseModel):
    id: int
    name: str
    display_name: str
    status: str


class RequirementTaskOut(BaseModel):
    requirement_item_id: int
    requirement_item_title: str
    requirement_lifecycle_status: str
    group_id: int | None = None
    group_name: str | None = None
    case_count: int
    not_run: int
    running: int
    passed: int
    failed: int
    attention_changed: int
    auto_discovery_enabled: bool = True


class RequirementItemAutoDiscoveryIn(BaseModel):
    enabled: bool


class RequirementItemAutoDiscoveryOut(BaseModel):
    requirement_item_id: int
    auto_discovery_enabled: bool


class PoolCardRoleOut(BaseModel):
    label: str
    names: list[str] = []


class PoolCardSprintOut(BaseModel):
    id: str
    name: str


class PoolCardOut(BaseModel):
    number: int | str | None = None
    status: str | None = None
    created_date: str | None = None
    link: str | None = None
    roles: list[PoolCardRoleOut] = []
    sprints: list[PoolCardSprintOut] = []


class RequirementItemOut(BaseModel):
    id: int
    group_id: int | None = None
    title: str
    status: str
    version: str | None = None
    lifecycle_status: str
    source_space: str | None = None
    tester_user_ids: list[int] = []
    card: PoolCardOut | None = None


class RequirementPoolOut(BaseModel):
    id: int
    external_key: str
    title: str
    description: str | None = None
    source_type: str
    status: str
    lifecycle_status: str
    source_space: str | None = None
    owner_user_id: int | None = None
    owner_name: str | None = None
    # 全部参与 QA 的内部 user_id（含主负责人）；项目池“测试人员”筛选按“参与即匹配”。
    tester_user_ids: list[int] = []
    card: PoolCardOut | None = None
    bound_group_id: int | None = None
    bound_group_name: str | None = None
    bound_item_id: int | None = None
    bound_item_title: str | None = None


class RequirementGroupOut(BaseModel):
    id: int
    name: str
    status: str
    items: list[RequirementItemOut]


class RequirementCatalogOut(BaseModel):
    groups: list[RequirementGroupOut]
    ungrouped_items: list[RequirementItemOut]
    total: int = 0
    page: int = 1
    page_size: int = 0
    filter_user_ids: list[int] = []
    sprints: list[PoolCardSprintOut] = []


class RequirementPoolSelectionIn(BaseModel):
    pool_id: int
    version: str


class RequirementPoolCreateItemsIn(BaseModel):
    pool_ids: list[int]


class RequirementPoolPageOut(BaseModel):
    items: list[RequirementPoolOut]
    total: int
    attachable_total: int
    page: int
    page_size: int
    filter_user_ids: list[int] = []
    sprints: list[PoolCardSprintOut] = []


class RequirementGroupCreateWithPoolIn(BaseModel):
    name: str
    description: str | None = None
    items: list[RequirementPoolSelectionIn]


class RequirementGroupAddPoolIn(BaseModel):
    items: list[RequirementPoolSelectionIn]


class RequirementItemVersionUpdateIn(BaseModel):
    version: str


class RequirementItemBindSelectionIn(BaseModel):
    requirement_item_id: int
    version: str


class RequirementGroupBindItemsIn(BaseModel):
    items: list[RequirementItemBindSelectionIn]


class RequirementGroupMutationOut(BaseModel):
    message: str
    group: RequirementGroupOut


class RequirementItemsMutationOut(BaseModel):
    message: str
    items: list[RequirementItemOut]


class RequirementItemMutationOut(BaseModel):
    message: str
    item: RequirementItemOut


class HomeSummaryOut(BaseModel):
    requirements: int
    case_count: int
    not_run: int
    running: int
    passed: int
    failed: int
    attention_changed: int


class HomeDashboardOut(BaseModel):
    user: UserOut
    summary: HomeSummaryOut
    requirements: list[RequirementTaskOut]


class CaseWorkbenchItemOut(BaseModel):
    id: int
    batch_id: int
    ordinal: int
    # 展示用序号：单测试集=纯数字(1,2,3)；多测试集=「集号-集内序号」(1-3、2-15)，稳定不浮动。
    display_no: str = ""
    suite_title: str
    source_name: str
    asset_status: str
    module_name: str
    product_feature: str
    test_feature: str
    raw_title: str
    clean_title: str
    path: str
    path_nodes: list[dict[str, Any]] = []
    scenario_tags: list[str]
    manual: bool
    execution_status: str
    coverage: dict[str, str] = {}
    lifecycle_state: str
    attention_reason: str | None = None
    case_type: str
    execution_target: str
    tag_source: str | None = None
    tag_reason: str | None = None
    tag_confidence: int
    run_enabled: bool
    report_url: str | None = None
    failure_type: str | None = None
    failure_summary: str | None = None
    bug_url: str | None = None
    # 已提交 bug 列表（支持一条 case 多次提交）：[{url, id}]
    bugs: list[dict[str, str]] = []
    # 后台草稿是否已就绪（用于前端按钮“后台准备中”波浪动画）。
    diagnosis_ready: bool = False
    bug_draft_ready: bool = False
    external_submission_id: str | None = None
    execution_started_at: datetime | None = None
    execution_finished_at: datetime | None = None
    preconditions: str
    steps_text: str
    expected_result: str

    model_config = ConfigDict(from_attributes=True)


class CaseWorkItemUpdateIn(BaseModel):
    case_id: int
    execution_status: str | None = None
    execution_target: str | None = None
    run_enabled: bool | None = None


class CaseWorkItemUpdateOut(BaseModel):
    case_id: int
    execution_status: str
    execution_target: str
    run_enabled: bool


class CaseCoverageUpdateIn(BaseModel):
    case_id: int
    lane: str
    state: str


class CaseCoverageOut(BaseModel):
    case_id: int
    coverage: dict[str, str] = {}


class CaseSuiteMarkdownExportOut(BaseModel):
    batch_id: int
    suite_title: str
    filename: str
    content: str
    case_count: int


class CaseSuiteMutationOut(BaseModel):
    requirement_item_id: int
    batch_id: int
    suite_title: str
    deleted_case_count: int
    deleted_running_count: int = 0
    deleted_batch_id: int
    message: str


class CaseAssetUpdateIn(BaseModel):
    module_name: str | None = None
    product_feature: str | None = None
    test_feature: str | None = None
    raw_title: str | None = None
    clean_title: str | None = None
    preconditions: str | None = None
    steps_text: str | None = None
    expected_result: str | None = None


class CaseAssetCreateIn(BaseModel):
    requirement_item_id: int
    batch_id: int
    path_nodes: list[dict[str, Any]]
    raw_title: str
    preconditions: str = ""
    steps_text: str = ""
    expected_result: str = ""


class CaseAssetMutationOut(BaseModel):
    case_id: int
    message: str
    deleted_batch_id: int | None = None
