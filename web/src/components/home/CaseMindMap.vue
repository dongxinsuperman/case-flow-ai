<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import type { CaseListItem, CasePathNode, ExecutionStatus, RequirementTask } from '../../types/case'
import PathGroupBranch from './CaseMindMapPathBranch.vue'

const props = defineProps<{
  cases: CaseListItem[]
  requirement: RequirementTask | null
  selectedCaseId: number | null
}>()

const emit = defineEmits<{
  selectCase: [caseId: number]
}>()

interface FieldNode {
  nodeId: string
  label: string
  value: string
}

interface MindCaseNode {
  nodeId: string
  item: CaseListItem
  fields: FieldNode[]
}

interface PathGroup {
  nodeId: string
  parentNodeId: string
  label: string
  name: string
  levelIndex: number
  count: number
  children: PathGroup[]
  cases: MindCaseNode[]
}

interface BuildPathGroup {
  label: string
  name: string
  levelIndex: number
  count: number
  children: BuildPathGroup[]
  childMap: Map<string, BuildPathGroup>
  cases: CaseListItem[]
}

interface SuiteGroup {
  nodeId: string
  batchId: number
  name: string
  count: number
  pathGroups: PathGroup[]
  cases: MindCaseNode[]
}

interface BuildSuiteGroup {
  batchId: number
  name: string
  count: number
  pathGroups: BuildPathGroup[]
  pathMap: Map<string, BuildPathGroup>
  cases: CaseListItem[]
}

const viewportRef = ref<HTMLElement | null>(null)
const stageRef = ref<HTMLElement | null>(null)
const mapRef = ref<HTMLElement | null>(null)
const contentRef = ref<HTMLElement | null>(null)
const zoom = ref(0.7)
const svgSize = reactive({ width: 0, height: 0 })
const connectorPaths = ref<string[]>([])

const statusLabels: Record<ExecutionStatus, string> = {
  not_run: '未执行',
  running: '执行中',
  passed: '通过',
  failed: '失败',
}

function statusClass(status: ExecutionStatus) {
  return `status-${status.replace('_', '-')}`
}

function caseNode(item: CaseListItem): MindCaseNode {
  return {
    nodeId: `case-${item.id}`,
    item,
    fields: [
      {
        nodeId: `pre-${item.id}`,
        label: '前置条件',
        value: item.preconditions || '无',
      },
      {
        nodeId: `steps-${item.id}`,
        label: '操作步骤',
        value: item.stepsText || '无',
      },
      {
        nodeId: `expected-${item.id}`,
        label: '预期结果',
        value: item.expectedResult || '无',
      },
    ],
  }
}

function itemPathNodes(item: CaseListItem): CasePathNode[] {
  const configured = (item.pathNodes || [])
    .map((node) => ({
      level: node.level,
      label: node.label || '层级',
      rawText: node.rawText,
      displayText: node.displayText || node.rawText || '',
    }))
    .filter((node) => node.displayText)
  if (configured.length) {
    return configured
  }
  return []
}

function makePathKey(label: string, name: string) {
  return `${label}\u0000${name}`
}

function normalizePathGroup(group: BuildPathGroup, nodeId: string, parentNodeId: string): PathGroup {
  return {
    nodeId,
    parentNodeId,
    label: group.label,
    name: group.name,
    levelIndex: group.levelIndex,
    count: group.count,
    children: group.children.map((child, index) => normalizePathGroup(child, `${nodeId}-path-${index}`, nodeId)),
    cases: group.cases.map(caseNode),
  }
}

const suites = computed<SuiteGroup[]>(() => {
  const suiteMap = new Map<number, BuildSuiteGroup>()
  const suiteGroups: BuildSuiteGroup[] = []

  for (const item of props.cases) {
    const suiteName = item.suiteTitle || '测试集无'
    let suite = suiteMap.get(item.batchId)
    if (!suite) {
      suite = {
        batchId: item.batchId,
        name: suiteName,
        count: 0,
        pathGroups: [],
        pathMap: new Map<string, BuildPathGroup>(),
        cases: [],
      }
      suiteMap.set(item.batchId, suite)
      suiteGroups.push(suite)
    }
    suite.count += 1

    const pathNodes = itemPathNodes(item)
    let groups = suite.pathGroups
    let groupMap = suite.pathMap
    let current: BuildPathGroup | null = null
    for (const [levelIndex, node] of pathNodes.entries()) {
      const key = makePathKey(node.label, node.displayText)
      let group = groupMap.get(key)
      if (!group) {
        group = {
          label: node.label,
          name: node.displayText,
          levelIndex,
          count: 0,
          children: [],
          childMap: new Map<string, BuildPathGroup>(),
          cases: [],
        }
        groupMap.set(key, group)
        groups.push(group)
      }
      group.count += 1
      current = group
      groups = group.children
      groupMap = group.childMap
    }
    if (current) {
      current.cases.push(item)
    } else {
      suite.cases.push(item)
    }
  }

  return suiteGroups.map((suite) => {
    const suiteNodeId = `suite-${suite.batchId}`
    return {
      nodeId: suiteNodeId,
      batchId: suite.batchId,
      name: suite.name,
      count: suite.count,
      pathGroups: suite.pathGroups.map((group, index) => normalizePathGroup(group, `${suiteNodeId}-path-${index}`, suiteNodeId)),
      cases: suite.cases.map(caseNode),
    }
  })
})

const zoomLabel = computed(() => `${Math.round(zoom.value * 100)}%`)

// 折叠的分组节点 id（测试集/模块/功能点/测试功能点）；case 标题及其字段不折叠。
const collapsed = ref(new Set<string>())
function isCollapsed(nodeId: string): boolean {
  return collapsed.value.has(nodeId)
}
function toggleCollapse(nodeId: string) {
  const next = new Set(collapsed.value)
  if (next.has(nodeId)) {
    next.delete(nodeId)
  } else {
    next.add(nodeId)
  }
  collapsed.value = next
  queueMindMapLayout({ center: false })
}

function setZoom(nextZoom: number) {
  zoom.value = Math.min(1.3, Math.max(0.35, Number(nextZoom.toFixed(2))))
  queueMindMapLayout({ center: false })
}

function resetMindMap() {
  zoom.value = 0.7
  queueMindMapLayout({ center: true })
}

function selectCase(caseId: number) {
  emit('selectCase', caseId)
}

function queueMindMapLayout(options: { center?: boolean } = {}) {
  const shouldCenter = options.center !== false
  const layout = () => {
    syncMindMapCanvas()
    if (shouldCenter) {
      centerMindMapViewport()
    }
  }

  void nextTick(() => {
    syncMindMapCanvas()
    window.requestAnimationFrame(() => {
      layout()
      window.requestAnimationFrame(layout)
    })
    window.setTimeout(layout, 80)
    window.setTimeout(layout, 240)
  })
}

function syncMindMapCanvas() {
  const viewport = viewportRef.value
  const stage = stageRef.value
  const map = mapRef.value
  const content = contentRef.value
  if (!viewport || !stage || !map || !content) {
    connectorPaths.value = []
    return
  }

  const scale = zoom.value
  const contentWidth = content.scrollWidth + 48
  const contentHeight = content.scrollHeight + 48
  const scaledWidth = contentWidth * scale
  const scaledHeight = contentHeight * scale
  const viewportWidth = Math.max(0, viewport.clientWidth - 16)
  const viewportHeight = Math.max(0, viewport.clientHeight - 16)
  const stageWidth = Math.max(scaledWidth, viewportWidth)
  const stageHeight = Math.max(scaledHeight, viewportHeight)
  const offsetX = Math.max(0, (stageWidth - scaledWidth) / 2)
  const offsetY = Math.max(0, (stageHeight - scaledHeight) / 2)

  map.style.width = `${contentWidth}px`
  map.style.height = `${contentHeight}px`
  map.style.left = `${offsetX}px`
  map.style.top = `${offsetY}px`
  stage.style.width = `${stageWidth}px`
  stage.style.height = `${stageHeight}px`

  drawMindMapLines()
}

function centerMindMapViewport() {
  const viewport = viewportRef.value
  if (!viewport) {
    return
  }
  const target = props.selectedCaseId
    ? viewport.querySelector<HTMLElement>(`.mind-case-node[data-case-id="${props.selectedCaseId}"]`)
    : viewport.querySelector<HTMLElement>('.xmind-root')
  if (!target) {
    return
  }

  const containerRect = viewport.getBoundingClientRect()
  const targetRect = target.getBoundingClientRect()
  const targetCenterY = viewport.scrollTop + targetRect.top - containerRect.top + targetRect.height / 2
  const nextTop = targetCenterY - viewport.clientHeight / 2
  const maxTop = Math.max(0, viewport.scrollHeight - viewport.clientHeight)

  viewport.scrollTop = Math.min(maxTop, Math.max(0, nextTop))
}

function drawMindMapLines() {
  const map = mapRef.value
  if (!map) {
    connectorPaths.value = []
    return
  }

  const scale = zoom.value
  const mapRect = map.getBoundingClientRect()
  const nodes = [...map.querySelectorAll<HTMLElement>('.xmind-connect-node')]
  const nodeById = new Map(nodes.map((node) => [node.dataset.nodeId, node]))
  const paths: string[] = []

  for (const node of nodes) {
    const parentId = node.dataset.parentId
    if (!parentId) {
      continue
    }
    const parent = nodeById.get(parentId)
    if (!parent) {
      continue
    }

    const parentRect = parent.getBoundingClientRect()
    const nodeRect = node.getBoundingClientRect()
    const x1 = (parentRect.right - mapRect.left) / scale
    const y1 = (parentRect.top + parentRect.height / 2 - mapRect.top) / scale
    const x2 = (nodeRect.left - mapRect.left) / scale
    const y2 = (nodeRect.top + nodeRect.height / 2 - mapRect.top) / scale
    const gap = Math.max(28, (x2 - x1) / 2)
    const mid1 = x1 + gap
    const mid2 = x2 - gap
    paths.push(`M ${x1} ${y1} C ${mid1} ${y1}, ${mid2} ${y2}, ${x2} ${y2}`)
  }

  svgSize.width = Number.parseFloat(map.style.width) || 0
  svgSize.height = Number.parseFloat(map.style.height) || 0
  connectorPaths.value = paths
}

function handleResize() {
  queueMindMapLayout({ center: false })
}

watch(
  () => props.cases,
  () => queueMindMapLayout({ center: false }),
  { deep: true },
)

watch(
  () => props.requirement?.requirementItemId,
  () => queueMindMapLayout({ center: true }),
)

watch(
  () => props.selectedCaseId,
  () => queueMindMapLayout({ center: true }),
)

onMounted(() => {
  window.addEventListener('resize', handleResize)
  queueMindMapLayout({ center: true })
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
})
</script>

<template>
  <section class="panel mind-map-panel">
    <div class="panel-head">
      <h2>状态脑图</h2>
      <div class="map-toolbar">
        <button type="button" @click="setZoom(zoom - 0.1)">-</button>
        <span>{{ zoomLabel }}</span>
        <button type="button" @click="setZoom(zoom + 0.1)">+</button>
        <button type="button" @click="resetMindMap">重置</button>
        <em>{{ cases.length }} 条</em>
      </div>
    </div>

    <div ref="viewportRef" class="home-mind-map">
      <div v-if="!cases.length" class="empty-state">当前需求暂无 case。</div>
      <div
        v-else
        ref="stageRef"
        class="xmind-stage"
      >
        <div
          ref="mapRef"
          class="xmind-map"
          :style="{ transform: `scale(${zoom})` }"
        >
          <svg
            class="xmind-lines"
            :width="svgSize.width"
            :height="svgSize.height"
            aria-hidden="true"
          >
            <path v-for="path in connectorPaths" :key="path" :d="path" />
          </svg>

          <div ref="contentRef" class="xmind-content">
            <div class="xmind-root xmind-connect-node" data-node-id="root">
              <strong>{{ requirement?.groupName || '未进入目录' }}</strong>
              <span>{{ requirement?.requirementItemTitle || '当前需求' }}</span>
            </div>

            <div class="xmind-branches">
              <section v-for="suite in suites" :key="suite.nodeId" class="xmind-row xmind-suite-row">
                <div
                  class="xmind-node xmind-node-suite xmind-connect-node xmind-collapsible"
                  :class="{ 'is-collapsed': isCollapsed(suite.nodeId) }"
                  :data-node-id="suite.nodeId"
                  data-parent-id="root"
                  @click="toggleCollapse(suite.nodeId)"
                >
                  <i class="xmind-caret">{{ isCollapsed(suite.nodeId) ? `▸ ${suite.count}` : '▾' }}</i>
                  <span>测试集</span>
                  <strong>{{ suite.name }}</strong>
                  <em>{{ suite.count }} 条</em>
                </div>

                <div v-if="!isCollapsed(suite.nodeId)" class="xmind-children">
                  <PathGroupBranch
                    v-for="group in suite.pathGroups"
                    :key="group.nodeId"
                    :group="group"
                    :selected-case-id="selectedCaseId"
                    :collapsed="collapsed"
                    :status-labels="statusLabels"
                    @toggle="toggleCollapse"
                    @select="selectCase"
                  />
                  <section v-for="caseNode in suite.cases" :key="caseNode.nodeId" class="xmind-row xmind-case-row">
                    <button
                      type="button"
                      class="mind-case-node xmind-node xmind-node-case xmind-connect-node"
                      :class="[statusClass(caseNode.item.executionStatus), { active: selectedCaseId === caseNode.item.id }]"
                      :data-node-id="caseNode.nodeId"
                      :data-parent-id="suite.nodeId"
                      :data-case-id="caseNode.item.id"
                      :title="caseNode.item.rawTitle"
                      @click="selectCase(caseNode.item.id)"
                    >
                      <span>测试标题 #{{ caseNode.item.displayNo || caseNode.item.ordinal }}</span>
                      <strong>{{ caseNode.item.rawTitle }}</strong>
                      <em>{{ statusLabels[caseNode.item.executionStatus] }}</em>
                    </button>

                    <div class="xmind-children xmind-field-children">
                      <div v-for="field in caseNode.fields" :key="field.nodeId" class="xmind-row xmind-field-row">
                        <div
                          class="xmind-node xmind-node-field xmind-connect-node"
                          :data-node-id="field.nodeId"
                          :data-parent-id="caseNode.nodeId"
                        >
                          <span>{{ field.label }}</span>
                          <strong>{{ field.value }}</strong>
                        </div>
                      </div>
                    </div>
                  </section>
                </div>
              </section>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
