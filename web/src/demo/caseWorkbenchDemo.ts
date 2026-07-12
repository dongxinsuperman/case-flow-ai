import type {
  AIPhoneDeviceList,
  AIPhoneSubmitResult,
  BugDraft,
  BugField,
  BugSubmitResult,
  CaseAssetUpdate,
  CaseListItem,
  CasePlatformResult,
  CaseWorkItemUpdate,
  CoverageLane,
  CoverageState,
  ExecutionStatus,
  ExecutionTarget,
  HomeDashboard,
  HomeSummary,
  RepairDraft,
  RepairPreview,
  RepairApplyResult,
  RequirementTask,
  User,
} from '../types/case'

type DemoCase = CaseListItem & {
  requirementItemId: number
}

interface DemoRequirement {
  requirementItemId: number
  requirementItemTitle: string
  requirementLifecycleStatus: string
  groupId: number
  groupName: string
}

interface DemoState {
  users: User[]
  requirements: DemoRequirement[]
  cases: DemoCase[]
  nextBugId: number
}

const DEMO_REPAIR_DRAFT_OFFSET = 5000
const DEMO_FAILURE_IMAGE = '/demo/failure-code-error.png'

let demoState: DemoState = createInitialDemoState()

export function resetDemoWorkbench() {
  demoState = createInitialDemoState()
}

export function getDemoUsers(): User[] {
  return clone(demoState.users)
}

export function getDemoHomeDashboard(userId: number): HomeDashboard {
  const user = demoState.users.find((item) => item.id === userId) ?? demoState.users[0]
  const requirements = demoState.requirements.map((requirement) => {
    const counts = countsForRequirement(requirement.requirementItemId)
    return {
      ...requirement,
      ...counts,
      autoDiscoveryEnabled: true,
    }
  })
  const summary = requirements.reduce<HomeSummary>(
    (memo, item) => ({
      requirements: memo.requirements + 1,
      caseCount: memo.caseCount + item.caseCount,
      notRun: memo.notRun + item.notRun,
      running: memo.running + item.running,
      passed: memo.passed + item.passed,
      failed: memo.failed + item.failed,
      attentionChanged: memo.attentionChanged + item.attentionChanged,
    }),
    {
      requirements: 0,
      caseCount: 0,
      notRun: 0,
      running: 0,
      passed: 0,
      failed: 0,
      attentionChanged: 0,
    },
  )

  return clone({
    user,
    summary,
    requirements,
  })
}

export function listDemoCases(requirementItemId: number): CaseListItem[] {
  return demoState.cases
    .filter((item) => item.requirementItemId === requirementItemId)
    .sort((left, right) => left.ordinal - right.ordinal)
    .map(toCaseListItem)
}

export function updateDemoCaseWorkItem(payload: CaseWorkItemUpdate) {
  const item = findDemoCase(payload.caseId)
  if (!item) {
    return
  }
  if (payload.executionTarget !== undefined) {
    item.executionTarget = payload.executionTarget
  }
  if (payload.runEnabled !== undefined) {
    item.runEnabled = payload.runEnabled
  }
  if (payload.executionStatus !== undefined) {
    setDemoCaseStatus(item, payload.executionStatus)
  }
}

export function updateDemoCaseAsset(caseId: number, payload: CaseAssetUpdate) {
  const item = findDemoCase(caseId)
  if (!item) {
    return
  }
  if (payload.rawTitle !== undefined && payload.rawTitle.trim()) {
    const rawTitle = payload.rawTitle.trim()
    const tagPrefix = rawTitle.match(/^(?:【[^】]+】)+/)?.[0] || ''
    const titleTags = [...tagPrefix.matchAll(/【([^】]+)】/g)].map((tag) => tag[1])
    item.rawTitle = rawTitle
    item.cleanTitle = rawTitle
    item.scenarioTags = titleTags.filter((tag) => tag !== '人工')
    item.manual = titleTags.includes('人工')
  } else if (payload.cleanTitle !== undefined) {
    throw new Error('更新演示 Case 标题必须提交完整标题 rawTitle')
  }
  if (payload.preconditions !== undefined) {
    item.preconditions = payload.preconditions.trim()
  }
  if (payload.stepsText !== undefined) {
    item.stepsText = payload.stepsText.trim()
  }
  if (payload.expectedResult !== undefined) {
    item.expectedResult = payload.expectedResult.trim()
  }
}

export function getDemoCasePlatformResults(caseId: number): CasePlatformResult[] {
  const item = findDemoCase(caseId)
  if (!item || !item.coverage) {
    return []
  }
  return Object.entries(item.coverage)
    .filter(([, state]) => state && state !== 'none')
    .map(([platform, state]) => ({
      platform,
      state: String(state),
      reportUrl: item.reportUrl || `https://example.com/demo/report/${platform}`,
      runId: `demo-run-${platform}`,
      statusReason: state === 'failed' ? item.failureSummary || '演示报告显示该端失败。' : null,
    }))
}

export function cycleDemoCaseCoverage(caseId: number, lane: CoverageLane) {
  const item = findDemoCase(caseId)
  if (!item) {
    return
  }
  const next: Record<CoverageState, CoverageState> = {
    none: 'passed',
    passed: 'failed',
    failed: 'none',
  }
  const current = item.coverage ?? {}
  item.coverage = { ...current, [lane]: next[current[lane] ?? 'none'] }
}

export function updateDemoCasesStatus(caseIds: number[], executionStatus: ExecutionStatus) {
  const selected = new Set(caseIds)
  for (const item of demoState.cases) {
    if (selected.has(item.id)) {
      setDemoCaseStatus(item, executionStatus)
    }
  }
}

export function listDemoAIPhoneDevices(): AIPhoneDeviceList {
  return clone({
    source: 'demo',
    devices: [
      {
        alias: 'demo-android-01',
        serial: 'DEMOANDROID01',
        platform: 'android',
        brand: 'Google',
        model: 'Pixel 8',
        osVersion: 'Android 15',
        occupancy: 'idle',
      },
      {
        alias: 'demo-ios-01',
        serial: 'DEMOIOS01',
        platform: 'ios',
        brand: 'Apple',
        model: 'iPhone 15',
        osVersion: 'iOS 18',
        occupancy: 'idle',
      },
      {
        alias: 'demo-android-busy',
        serial: 'DEMOANDROIDBUSY',
        platform: 'android',
        brand: 'Samsung',
        model: 'Galaxy S24',
        osVersion: 'Android 14',
        occupancy: 'busy',
        lockHolderType: 'demo',
      },
    ],
    error: null,
  })
}

export function submitDemoAIPhoneCases(
  caseIds: number[],
  submissionName: string,
): AIPhoneSubmitResult {
  const submissionId = `demo-submission-${Date.now()}`
  for (const caseId of caseIds) {
    const item = findDemoCase(caseId)
    if (item) {
      setDemoCaseStatus(item, 'running')
      item.externalSubmissionId = submissionId
    }
  }

  return clone({
    submissionId,
    submissionName,
    callbackUrl: 'demo://case-flow/aiphone/callback',
    batchId: Math.floor(Date.now() / 1000),
    submittedCount: caseIds.length,
    response: {
      mode: 'demo',
      message: '演示模式已模拟提交，不会访问 AI Phone。',
    },
  })
}

export function previewDemoRepairs(caseIds: number[]): RepairPreview {
  const items = caseIds
    .map((caseId) => findDemoCase(caseId))
    .filter((item): item is DemoCase => Boolean(item))
    .map((item) => {
      item.diagnosisReady = true
      item.bugDraftReady = true
      return buildRepairDraft(item)
    })
  return clone({ items })
}

export function getDemoBugDraft(caseId: number): BugDraft {
  const item = findDemoCase(caseId)
  if (!item) {
    throw new Error('演示 case 不存在')
  }
  item.bugDraftReady = true
  return clone({
    caseId: item.id,
    space: 'demo_app',
    title: `[演示] ${item.rawTitle}`,
    description: [
      `诊断原因：${item.failureSummary || '演示报告中发现关键断言不满足。'}`,
      '',
      `路径：${item.path}`,
      '',
      `前置条件：${item.preconditions}`,
      '',
      `操作步骤：${item.stepsText}`,
      '',
      `预期结果：${item.expectedResult}`,
    ].join('\n'),
    fields: buildBugFields(),
    hasDiagnosisImage: true,
    keyImage: DEMO_FAILURE_IMAGE,
    existingBugUrl: item.bugUrl ?? null,
    submittedBugs: (item.bugs ?? []).map((b) => ({ url: b.url, id: b.id })),
  })
}

export function submitDemoBug(caseId: number): BugSubmitResult {
  const item = findDemoCase(caseId)
  if (!item) {
    throw new Error('演示 case 不存在')
  }
  const bugId = demoState.nextBugId++
  const bugUrl = `https://project.feishu.cn/demo_app/issue/detail/DEMO-BUG-${bugId}`
  item.bugUrl = bugUrl
  // 追加到已提交列表（支持多次提交）。
  item.bugs = [...(item.bugs ?? []), { url: bugUrl, id: String(bugId) }]
  return clone({
    caseId,
    bugId,
    bugUrl,
    submittedCount: item.bugs.length,
    message: '演示模式已模拟提交 bug，不会访问飞书项目。',
  })
}

export function applyDemoRepairDraft(
  draftId: number,
  edited?: { stepsText?: string; preconditions?: string; expectedResult?: string },
): RepairApplyResult {
  const caseId = draftId - DEMO_REPAIR_DRAFT_OFFSET
  const item = findDemoCase(caseId)
  if (!item) {
    throw new Error('演示修复草稿不存在')
  }
  if (edited?.preconditions !== undefined) {
    item.preconditions = edited.preconditions.trim()
  }
  if (edited?.stepsText !== undefined) {
    item.stepsText = edited.stepsText.trim()
  }
  void edited?.expectedResult
  setDemoCaseStatus(item, 'not_run')
  item.attentionReason = '变更待确认'
  return clone({
    caseId,
    message: '演示模式已应用修复建议，Case 已回到待验证。',
  })
}

function createInitialDemoState(): DemoState {
  const now = Date.now()
  return {
    users: [
      {
        id: 9001,
        name: 'demo_tester',
        displayName: '演示测试同学',
        status: 'active',
      },
    ],
    requirements: [
      {
        requirementItemId: 9101,
        requirementItemTitle: '登录与学习页 V1.0',
        requirementLifecycleStatus: '测试中',
        groupId: 9001,
        groupName: '示例 App 项目',
      },
      {
        requirementItemId: 9102,
        requirementItemTitle: '支付与订单回归 V2.1',
        requirementLifecycleStatus: '测试中',
        groupId: 9002,
        groupName: '示例交易链路',
      },
    ],
    cases: [
      createCase(101, 9101, 1, '登录冒烟测试集', '账号登录', '手机号登录', '验证码登录', '验证码正确时登录成功', 'not_run', 'app', {
        coverage: { android: 'passed' },
      }),
      createCase(102, 9101, 2, '登录冒烟测试集', '账号登录', '手机号登录', '验证码登录', '验证码错误时出现错误提示', 'failed', 'app', {
        failureType: 'assertion_failed',
        failureSummary: '页面未出现预期的验证码错误文案。',
        reportUrl: 'https://example.com/demo/report/login-code',
        diagnosisReady: true,
        bugDraftReady: true,
        coverage: { android: 'failed', ios: 'passed' },
      }),
      createCase(103, 9101, 3, '登录冒烟测试集', '学习页', '课程卡片', '卡片渲染', '进入学习页展示课程卡片', 'passed', 'web', {
        coverage: { chrome: 'passed', safari: 'passed', firefox: 'none' },
      }),
      createCase(104, 9101, 4, '登录冒烟测试集', '学习页', '课程卡片', '卡片操作', '点击更多进入课程详情', 'running', 'app', {
        executionStartedAt: new Date(now - 7 * 60 * 1000).toISOString(),
        externalSubmissionId: 'demo-running-001',
        coverage: { android: 'passed', harmony: 'failed' },
      }),
      createCase(105, 9101, 5, '登录冒烟测试集', '学习页', '学习记录', '后台核对', '运营后台核对学习记录落库', 'not_run', 'manual', {
        attentionReason: '变更待确认',
      }),
      createCase(106, 9101, 6, '登录冒烟测试集', '学习页', '接口校验', '学习进度接口', '学习进度接口返回最新节点', 'failed', 'api', {
        failureType: 'business_failure',
        failureSummary: '接口返回的 latest_node 与 App 当前课程节点不一致。',
        reportUrl: 'https://example.com/demo/report/progress-api',
        diagnosisReady: true,
        bugDraftReady: false,
      }),
      createCase(201, 9102, 1, '支付回归测试集', '订单确认', '优惠券', '抵扣计算', '优惠券抵扣金额正确', 'passed', 'web', {
        coverage: { chrome: 'passed', safari: 'failed' },
      }),
      createCase(202, 9102, 2, '支付回归测试集', '订单确认', '地址', '默认地址', '进入确认页自动带出默认地址', 'not_run', 'app'),
      createCase(203, 9102, 3, '支付回归测试集', '收银台', '支付渠道', '微信支付', '选择微信支付后生成支付单', 'running', 'app', {
        executionStartedAt: new Date(now - 3 * 60 * 1000).toISOString(),
        externalSubmissionId: 'demo-running-002',
        coverage: { android: 'passed', ios: 'passed' },
      }),
      createCase(204, 9102, 4, '支付回归测试集', '收银台', '支付渠道', '余额不足', '余额不足时提示更换支付方式', 'failed', 'app', {
        failureType: 'case_step_failure',
        failureSummary: '测试步骤未先造出余额不足账户，导致执行器停在支付页。',
        reportUrl: 'https://example.com/demo/report/pay-balance',
        diagnosisReady: true,
        bugDraftReady: true,
      }),
      createCase(205, 9102, 5, '支付回归测试集', '订单结果', '结果页', '支付成功页', '支付完成后展示订单成功页', 'not_run', 'web'),
      createCase(206, 9102, 6, '支付回归测试集', '订单结果', '消息通知', '支付通知', '支付成功后发送站内消息', 'not_run', 'api', {
        attentionReason: '变更待确认',
      }),
    ],
    nextBugId: 3001,
  }
}

function createCase(
  id: number,
  requirementItemId: number,
  ordinal: number,
  suiteTitle: string,
  moduleName: string,
  productFeature: string,
  testFeature: string,
  rawTitle: string,
  executionStatus: ExecutionStatus,
  executionTarget: ExecutionTarget,
  overrides: Partial<DemoCase> = {},
): DemoCase {
  const displayNo = `${requirementItemId === 9101 ? 'L' : 'P'}-${String(ordinal).padStart(2, '0')}`
  const path = `${suiteTitle} / ${moduleName} / ${productFeature} / ${testFeature}`
  return {
    id,
    batchId: requirementItemId === 9101 ? 900101 : 900201,
    requirementItemId,
    ordinal,
    displayNo,
    suiteTitle,
    sourceName: 'demo-cases.md',
    assetStatus: 'active',
    moduleName,
    productFeature,
    testFeature,
    rawTitle,
    cleanTitle: rawTitle,
    path,
    pathNodes: [
      { level: 2, label: '模块', rawText: moduleName, displayText: moduleName },
      { level: 3, label: '功能点', rawText: productFeature, displayText: productFeature },
      { level: 4, label: '测试功能点', rawText: testFeature, displayText: testFeature },
    ],
    scenarioTags: [executionTarget === 'app' ? 'App' : executionTarget.toUpperCase()],
    manual: executionTarget === 'manual',
    executionStatus,
    coverage: {},
    lifecycleState: executionStatus === 'passed' ? '已验证' : '待验证',
    attentionReason: null,
    caseType: executionTarget === 'manual' ? 'manual' : 'auto',
    executionTarget,
    tagReason: executionTarget === 'manual' ? '包含后台核对，建议人工执行' : '演示模式按通道标签分类',
    tagConfidence: 95,
    runEnabled: executionStatus !== 'running',
    reportUrl: null,
    failureType: executionStatus === 'failed' ? 'execution_failed' : null,
    failureSummary: executionStatus === 'failed' ? '演示报告显示执行结果不符合预期。' : null,
    bugUrl: null,
    bugs: [],
    diagnosisReady: executionStatus === 'failed',
    bugDraftReady: executionStatus === 'failed',
    externalSubmissionId: null,
    executionStartedAt: executionStatus === 'running' ? new Date(Date.now() - 2 * 60 * 1000).toISOString() : null,
    executionFinishedAt: executionStatus === 'passed' || executionStatus === 'failed'
      ? new Date(Date.now() - 30 * 60 * 1000).toISOString()
      : null,
    preconditions: '演示账号已登录，测试环境数据已准备。',
    stepsText: [
      `打开 ${moduleName} 页面`,
      `进入「${productFeature}」功能`,
      `执行「${testFeature}」相关操作`,
      '观察页面、接口或后台数据是否符合预期',
    ].join('\n'),
    expectedResult: `${rawTitle}，且关键状态、文案、数据一致。`,
    ...overrides,
  }
}

function countsForRequirement(requirementItemId: number) {
  const cases = demoState.cases.filter((item) => item.requirementItemId === requirementItemId)
  return {
    caseCount: cases.length,
    notRun: cases.filter((item) => item.executionStatus === 'not_run').length,
    running: cases.filter((item) => item.executionStatus === 'running').length,
    passed: cases.filter((item) => item.executionStatus === 'passed').length,
    failed: cases.filter((item) => item.executionStatus === 'failed').length,
    attentionChanged: cases.filter((item) => item.attentionReason === '变更待确认').length,
  }
}

function findDemoCase(caseId: number): DemoCase | undefined {
  return demoState.cases.find((item) => item.id === caseId)
}

function setDemoCaseStatus(item: DemoCase, executionStatus: ExecutionStatus) {
  item.executionStatus = executionStatus
  item.runEnabled = executionStatus !== 'running'
  item.executionStartedAt = executionStatus === 'running' ? new Date().toISOString() : null
  item.executionFinishedAt = executionStatus === 'passed' || executionStatus === 'failed' ? new Date().toISOString() : null
  item.lifecycleState = executionStatus === 'passed' ? '已验证' : '待验证'
  if (executionStatus === 'failed') {
    item.failureType = item.failureType || 'execution_failed'
    item.failureSummary = item.failureSummary || '演示报告显示执行结果不符合预期。'
    item.reportUrl = item.reportUrl || 'https://example.com/demo/report/latest'
    item.diagnosisReady = true
    item.bugDraftReady = true
  } else {
    item.failureType = null
    item.failureSummary = null
    item.reportUrl = executionStatus === 'passed' ? 'https://example.com/demo/report/passed' : null
    item.bugUrl = null
    item.bugs = []
    item.diagnosisReady = false
    item.bugDraftReady = false
  }
}

function buildRepairDraft(item: DemoCase): RepairDraft {
  const draftId = DEMO_REPAIR_DRAFT_OFFSET + item.id
  const repairable = item.failureType !== 'execution_failed'
  const proposedPreconditions = item.preconditions.includes('演示账号')
    ? `${item.preconditions}\n补充：确保演示环境已刷新到最新测试数据。`
    : item.preconditions
  const proposedSteps = [
    item.stepsText,
    '补充校验：等待页面或接口状态稳定后再读取断言目标。',
  ].join('\n')
  return {
    draftId,
    caseId: item.id,
    caseTitle: item.rawTitle,
    path: item.path,
    status: 'ready',
    repairable,
    failureType: item.failureType || 'unknown_failure',
    reason: item.failureSummary || '演示报告缺少明确失败原因。',
    fixReason: repairable ? '演示模式根据失败摘要补充了数据准备和稳定等待步骤。' : '该失败更像环境或执行器异常，建议先重跑确认。',
    evidence: item.failureSummary || '演示报告中关键断言未满足。',
    keyImage: DEMO_FAILURE_IMAGE,
    repairChannel: repairable ? 'case_steps' : 'manual_review',
    process: [
      {
        round: 1,
        note: '读取演示报告摘要和 Case 步骤。',
        decision: repairable ? '可通过补充步骤降低失败概率。' : '暂不自动改 Case。',
      },
    ],
    originalSteps: item.stepsText,
    proposedSteps,
    originalPreconditions: item.preconditions,
    proposedPreconditions,
    originalExpected: item.expectedResult,
    proposedExpected: item.expectedResult,
    reportUrl: item.reportUrl,
    reportSummary: item.failureSummary || '演示报告显示执行失败。',
    bugUrl: item.bugUrl,
    modelName: 'demo-repair-model',
    gate: {
      label: repairable ? item.failureType : '执行失败',
      canRepair: repairable,
      reason: repairable ? '演示失败可通过优化 Case 步骤处理。' : '执行器或环境失败不自动修改 Case。',
    },
    createdAt: new Date().toISOString(),
  }
}

function buildBugFields(): BugField[] {
  return [
    {
      fieldKey: 'severity',
      label: '严重程度',
      type: 'select',
      editable: true,
      required: true,
      options: [
        { name: 'P1', id: 'demo_p1' },
        { name: 'P2', id: 'demo_p2' },
        { name: 'P3', id: 'demo_p3' },
      ],
      selected: 'P2',
      display: 'P2',
    },
    {
      fieldKey: 'issue_type',
      label: '问题类型',
      type: 'select',
      editable: true,
      required: true,
      options: [
        { name: '功能问题', id: 'demo_function' },
        { name: '兼容问题', id: 'demo_compat' },
        { name: '数据问题', id: 'demo_data' },
      ],
      selected: '功能问题',
      display: '功能问题',
    },
    {
      fieldKey: 'affected_channel',
      label: '影响端',
      type: 'multi_select',
      editable: true,
      required: false,
      options: [
        { name: 'App', id: 'demo_app' },
        { name: 'Web', id: 'demo_web' },
        { name: 'API', id: 'demo_api' },
      ],
      selected: ['App'],
      display: 'App',
    },
  ]
}

function toCaseListItem(item: DemoCase): CaseListItem {
  const {
    requirementItemId,
    ...caseItem
  } = item
  void requirementItemId
  return clone(caseItem)
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}
