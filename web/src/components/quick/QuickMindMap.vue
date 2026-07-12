<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import type { ExecutionStatus } from '../../types/case'
import type { QuickCaseItem } from '../../types/quick'
import QuickMindMapPathBranch from './QuickMindMapPathBranch.vue'

const props = defineProps<{
  cases: QuickCaseItem[]
  sessionTitle: string
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
  item: QuickCaseItem
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
  cases: QuickCaseItem[]
}

const viewportRef = ref<HTMLElement | null>(null)
const stageRef = ref<HTMLElement | null>(null)
const mapRef = ref<HTMLElement | null>(null)
const contentRef = ref<HTMLElement | null>(null)
const zoom = ref(0.72)
const svgSize = reactive({ width: 0, height: 0 })
const connectorPaths = ref<string[]>([])
const collapsed = ref(new Set<string>())

const statusLabels: Record<ExecutionStatus, string> = {
  not_run: '未执行',
  running: '执行中',
  passed: '通过',
  failed: '失败',
}

function statusClass(status: ExecutionStatus) {
  return `status-${status.replace('_', '-')}`
}

function caseNode(item: QuickCaseItem): MindCaseNode {
  return {
    nodeId: `case-${item.id}`,
    item,
    fields: [
      { nodeId: `pre-${item.id}`, label: '前置条件', value: item.preconditions || '无' },
      { nodeId: `steps-${item.id}`, label: '操作步骤', value: item.stepsText || '无' },
      { nodeId: `expected-${item.id}`, label: '预期结果', value: item.expectedResult || '无' },
    ],
  }
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

const pathGroups = computed<PathGroup[]>(() => {
  const roots: BuildPathGroup[] = []
  const rootMap = new Map<string, BuildPathGroup>()
  const directCases: QuickCaseItem[] = []

  for (const item of props.cases) {
    const nodes = (item.pathNodes || [])
      .map((node) => ({
        label: node.label || '层级',
        name: node.displayText || node.rawText || '',
      }))
      .filter((node) => node.name)
    if (!nodes.length) {
      directCases.push(item)
      continue
    }
    let groups = roots
    let groupMap = rootMap
    let current: BuildPathGroup | null = null
    for (const node of nodes) {
      const key = makePathKey(node.label, node.name)
      let group = groupMap.get(key)
      if (!group) {
        group = { label: node.label, name: node.name, levelIndex: nodes.indexOf(node), count: 0, children: [], childMap: new Map(), cases: [] }
        groupMap.set(key, group)
        groups.push(group)
      }
      group.count += 1
      current = group
      groups = group.children
      groupMap = group.childMap
    }
    current?.cases.push(item)
  }

  const normalized = roots.map((group, index) => normalizePathGroup(group, `path-${index}`, 'root'))
  if (directCases.length) {
    normalized.push({
      nodeId: 'path-direct',
      parentNodeId: 'root',
      label: '未分层',
      name: '直接 case',
      levelIndex: 0,
      count: directCases.length,
      children: [],
      cases: directCases.map(caseNode),
    })
  }
  return normalized
})

const zoomLabel = computed(() => `${Math.round(zoom.value * 100)}%`)

function isCollapsed(nodeId: string): boolean {
  return collapsed.value.has(nodeId)
}

function toggleCollapse(nodeId: string) {
  const next = new Set(collapsed.value)
  if (next.has(nodeId)) next.delete(nodeId)
  else next.add(nodeId)
  collapsed.value = next
  queueLayout({ center: false })
}

function setZoom(nextZoom: number) {
  zoom.value = Math.min(1.25, Math.max(0.35, Number(nextZoom.toFixed(2))))
  queueLayout({ center: false })
}

function resetMindMap() {
  zoom.value = 0.72
  queueLayout({ center: true })
}

function queueLayout(options: { center?: boolean } = {}) {
  const shouldCenter = options.center !== false
  const layout = () => {
    syncCanvas()
    if (shouldCenter) centerViewport()
  }
  void nextTick(() => {
    syncCanvas()
    window.requestAnimationFrame(() => {
      layout()
      window.requestAnimationFrame(layout)
    })
    window.setTimeout(layout, 80)
    window.setTimeout(layout, 240)
  })
}

function syncCanvas() {
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
  map.style.width = `${contentWidth}px`
  map.style.height = `${contentHeight}px`
  map.style.left = `${Math.max(0, (stageWidth - scaledWidth) / 2)}px`
  map.style.top = `${Math.max(0, (stageHeight - scaledHeight) / 2)}px`
  stage.style.width = `${stageWidth}px`
  stage.style.height = `${stageHeight}px`
  drawLines()
}

function centerViewport() {
  const viewport = viewportRef.value
  if (!viewport) return
  const target = props.selectedCaseId
    ? viewport.querySelector<HTMLElement>(`.mind-case-node[data-case-id="${props.selectedCaseId}"]`)
    : viewport.querySelector<HTMLElement>('.xmind-root')
  if (!target) return
  const containerRect = viewport.getBoundingClientRect()
  const targetRect = target.getBoundingClientRect()
  const targetCenterY = viewport.scrollTop + targetRect.top - containerRect.top + targetRect.height / 2
  viewport.scrollTop = Math.max(0, targetCenterY - viewport.clientHeight / 2)
}

function drawLines() {
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
    if (!parentId) continue
    const parent = nodeById.get(parentId)
    if (!parent) continue
    const parentRect = parent.getBoundingClientRect()
    const nodeRect = node.getBoundingClientRect()
    const x1 = (parentRect.right - mapRect.left) / scale
    const y1 = (parentRect.top + parentRect.height / 2 - mapRect.top) / scale
    const x2 = (nodeRect.left - mapRect.left) / scale
    const y2 = (nodeRect.top + nodeRect.height / 2 - mapRect.top) / scale
    const gap = Math.max(28, (x2 - x1) / 2)
    paths.push(`M ${x1} ${y1} C ${x1 + gap} ${y1}, ${x2 - gap} ${y2}, ${x2} ${y2}`)
  }
  svgSize.width = Number.parseFloat(map.style.width) || 0
  svgSize.height = Number.parseFloat(map.style.height) || 0
  connectorPaths.value = paths
}

function handleResize() {
  queueLayout({ center: false })
}

function selectCase(caseId: number) {
  emit('selectCase', caseId)
}

watch(() => props.cases, () => queueLayout({ center: false }), { deep: true })
watch(() => props.sessionTitle, () => queueLayout({ center: true }))
watch(() => props.selectedCaseId, () => queueLayout({ center: true }))

onMounted(() => {
  window.addEventListener('resize', handleResize)
  queueLayout({ center: true })
})

onBeforeUnmount(() => window.removeEventListener('resize', handleResize))
</script>

<template>
  <section class="panel mind-map-panel quick-map-panel">
    <div class="panel-head">
      <h2>快速脑图</h2>
      <div class="map-toolbar">
        <button type="button" @click="setZoom(zoom - 0.1)">-</button>
        <span>{{ zoomLabel }}</span>
        <button type="button" @click="setZoom(zoom + 0.1)">+</button>
        <button type="button" @click="resetMindMap">重置</button>
        <em>{{ cases.length }} 条</em>
      </div>
    </div>

    <div ref="viewportRef" class="home-mind-map">
      <div v-if="!cases.length" class="empty-state">当前 session 暂无 case。</div>
      <div v-else ref="stageRef" class="xmind-stage">
        <div ref="mapRef" class="xmind-map" :style="{ transform: `scale(${zoom})` }">
          <svg class="xmind-lines" :width="svgSize.width" :height="svgSize.height" aria-hidden="true">
            <path v-for="path in connectorPaths" :key="path" :d="path" />
          </svg>
          <div ref="contentRef" class="xmind-content">
            <div class="xmind-root xmind-connect-node" data-node-id="root">
              <strong>{{ sessionTitle || 'Quick Session' }}</strong>
              <span>Markdown 快速打磨</span>
            </div>
            <div class="xmind-branches">
              <section v-for="group in pathGroups" :key="group.nodeId" class="xmind-row xmind-suite-row">
                <div
                  class="xmind-node xmind-node-suite xmind-connect-node xmind-collapsible"
                  :class="{ 'is-collapsed': isCollapsed(group.nodeId) }"
                  :data-node-id="group.nodeId"
                  data-parent-id="root"
                  @click="toggleCollapse(group.nodeId)"
                >
                  <i class="xmind-caret">{{ isCollapsed(group.nodeId) ? `▸ ${group.count}` : '▾' }}</i>
                  <span>{{ group.label }}</span>
                  <strong>{{ group.name }}</strong>
                  <em>{{ group.count }} 条</em>
                </div>
                <div v-if="!isCollapsed(group.nodeId)" class="xmind-children">
                  <QuickMindMapPathBranch
                    v-for="child in group.children"
                    :key="child.nodeId"
                    :group="child"
                    :selected-case-id="selectedCaseId"
                    :collapsed="collapsed"
                    :status-labels="statusLabels"
                    @toggle="toggleCollapse"
                    @select="selectCase"
                  />
                  <section v-for="caseNode in group.cases" :key="caseNode.nodeId" class="xmind-row xmind-case-row">
                    <button
                      type="button"
                      class="mind-case-node xmind-node xmind-node-case xmind-connect-node"
                      :class="[statusClass(caseNode.item.executionStatus), { active: selectedCaseId === caseNode.item.id }]"
                      :data-node-id="caseNode.nodeId"
                      :data-parent-id="group.nodeId"
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
                        <div class="xmind-node xmind-node-field xmind-connect-node" :data-node-id="field.nodeId" :data-parent-id="caseNode.nodeId">
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
