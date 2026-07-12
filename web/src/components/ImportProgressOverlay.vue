<script setup lang="ts">
import { computed } from 'vue'

const DEFAULT_STAGES = ['解析 Markdown', '规则识别执行端', '智能打标中', '写入用例']

const props = withDefaults(defineProps<{
  open: boolean
  title: string
  filename?: string
  elapsedSeconds: number
  stageIndex: number
  variant?: 'standard' | 'quick'
  stages?: string[]
  hint?: string
}>(), {
  filename: '',
  variant: 'standard',
  stages: () => ['解析 Markdown', '规则识别执行端', '智能打标中', '写入用例'],
  hint: '',
})

const stages = computed(() => (props.stages.length ? props.stages : DEFAULT_STAGES))
const safeStageIndex = computed(() => Math.min(Math.max(props.stageIndex, 0), stages.value.length - 1))
const currentStage = computed(() => stages.value[safeStageIndex.value])
const elapsedText = computed(() => {
  const total = Math.max(0, Math.floor(props.elapsedSeconds || 0))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  if (hours > 0) {
    return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
  }
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
})
const waitMessage = computed(() => {
  if (props.hint) {
    return props.hint
  }
  if (props.elapsedSeconds >= 60) {
    return '模型仍在响应，超时或失败会自动按人工处理。'
  }
  if (props.elapsedSeconds >= 15) {
    return '正在等待模型完成剩余 case 的执行端判断。'
  }
  return props.variant === 'quick' ? '正在生成当前 Quick Session。' : '正在写入当前导入任务。'
})
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="import-progress-mask" :class="`is-${variant}`">
      <section class="import-progress-panel" aria-live="polite" aria-busy="true">
        <div class="import-progress-head">
          <div>
            <span>{{ variant === 'quick' ? 'Quick Import' : 'Case Import' }}</span>
            <h2>{{ title }}</h2>
            <p v-if="filename">{{ filename }}</p>
          </div>
          <div class="import-progress-timer">
            <small>已用时</small>
            <strong>{{ elapsedText }}</strong>
          </div>
        </div>

        <div class="import-progress-active">
          <span>{{ currentStage }}</span>
          <b>{{ waitMessage }}</b>
        </div>
        <div class="import-progress-flow" aria-hidden="true"></div>

        <ol class="import-progress-steps">
          <li
            v-for="(stage, index) in stages"
            :key="stage"
            :class="{ active: index === safeStageIndex, done: index < safeStageIndex }"
          >
            <i></i>
            <span>{{ stage }}</span>
          </li>
        </ol>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.import-progress-mask {
  position: fixed;
  inset: 0;
  z-index: 140;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(15, 23, 42, 0.48);
  backdrop-filter: blur(8px);
}

.import-progress-panel {
  position: relative;
  width: min(560px, 100%);
  overflow: hidden;
  border-radius: 8px;
  padding: 22px;
  box-shadow: 0 28px 90px rgba(15, 23, 42, 0.32);
}

.import-progress-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
}

.import-progress-head span,
.import-progress-timer small {
  display: block;
  margin-bottom: 5px;
  font-size: 11px;
  font-weight: 900;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.import-progress-head h2 {
  margin: 0;
  font-size: 24px;
  line-height: 1.15;
  letter-spacing: 0;
}

.import-progress-head p {
  margin: 8px 0 0;
  font-size: 13px;
  font-weight: 800;
  word-break: break-word;
}

.import-progress-timer {
  min-width: 116px;
  text-align: right;
}

.import-progress-timer strong {
  display: block;
  font-variant-numeric: tabular-nums;
  font-size: 34px;
  line-height: 1;
  letter-spacing: 0;
}

.import-progress-active {
  display: grid;
  gap: 6px;
  margin-top: 24px;
}

.import-progress-active span {
  font-size: 17px;
  font-weight: 900;
}

.import-progress-active b {
  min-height: 20px;
  font-size: 13px;
  line-height: 1.5;
}

.import-progress-flow {
  position: relative;
  height: 3px;
  overflow: hidden;
  margin: 18px 0 20px;
  border-radius: 999px;
}

.import-progress-flow::before {
  content: "";
  position: absolute;
  inset: 0;
  width: 42%;
  border-radius: inherit;
  animation: import-progress-flow 1.25s ease-in-out infinite;
}

.import-progress-steps {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.import-progress-steps li {
  display: grid;
  gap: 8px;
  min-width: 0;
  font-size: 12px;
  font-weight: 900;
}

.import-progress-steps i {
  width: 100%;
  height: 7px;
  border-radius: 999px;
}

.import-progress-steps span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.import-progress-mask.is-standard .import-progress-panel {
  border: 1px solid #d8e2f0;
  background: #ffffff;
  color: #172033;
}

.import-progress-mask.is-standard .import-progress-head span,
.import-progress-mask.is-standard .import-progress-timer small,
.import-progress-mask.is-standard .import-progress-head p,
.import-progress-mask.is-standard .import-progress-active b {
  color: #64748b;
}

.import-progress-mask.is-standard .import-progress-flow {
  background: #dbe7f7;
}

.import-progress-mask.is-standard .import-progress-flow::before {
  background: linear-gradient(90deg, #2563eb, #14b8a6);
}

.import-progress-mask.is-standard .import-progress-steps i {
  background: #dbe7f7;
}

.import-progress-mask.is-standard .import-progress-steps li.done i,
.import-progress-mask.is-standard .import-progress-steps li.active i {
  background: #2563eb;
}

.import-progress-mask.is-standard .import-progress-steps li.active span {
  color: #1d4ed8;
}

.import-progress-mask.is-quick {
  background:
    radial-gradient(circle at 50% 44%, rgba(34, 211, 238, 0.16), transparent 34%),
    rgba(2, 6, 23, 0.72);
}

.import-progress-mask.is-quick .import-progress-panel {
  border: 1px solid rgba(125, 211, 252, 0.38);
  background:
    linear-gradient(180deg, rgba(15, 23, 42, 0.92), rgba(15, 23, 42, 0.78)),
    repeating-linear-gradient(0deg, rgba(125, 211, 252, 0.055) 0 1px, transparent 1px 9px);
  color: #f8fbff;
  box-shadow:
    0 34px 110px rgba(0, 0, 0, 0.46),
    0 0 54px rgba(34, 211, 238, 0.18),
    inset 0 1px 0 rgba(255, 255, 255, 0.12);
}

.import-progress-mask.is-quick .import-progress-panel::before {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: linear-gradient(90deg, transparent, rgba(125, 211, 252, 0.11), transparent);
  transform: translateX(-100%);
  animation: import-progress-scan 3.2s linear infinite;
  pointer-events: none;
}

.import-progress-mask.is-quick .import-progress-head,
.import-progress-mask.is-quick .import-progress-active,
.import-progress-mask.is-quick .import-progress-flow,
.import-progress-mask.is-quick .import-progress-steps {
  position: relative;
}

.import-progress-mask.is-quick .import-progress-head span,
.import-progress-mask.is-quick .import-progress-timer small {
  color: #67e8f9;
}

.import-progress-mask.is-quick .import-progress-head p,
.import-progress-mask.is-quick .import-progress-active b {
  color: #a9bdd8;
}

.import-progress-mask.is-quick .import-progress-timer strong {
  color: #c8fff7;
  text-shadow: 0 0 22px rgba(45, 212, 191, 0.42);
}

.import-progress-mask.is-quick .import-progress-flow {
  background: rgba(51, 65, 85, 0.9);
}

.import-progress-mask.is-quick .import-progress-flow::before {
  background: linear-gradient(90deg, #22d3ee, #34d399, #fbbf24);
  box-shadow: 0 0 18px rgba(34, 211, 238, 0.52);
}

.import-progress-mask.is-quick .import-progress-steps i {
  background: rgba(51, 65, 85, 0.9);
}

.import-progress-mask.is-quick .import-progress-steps li.done i,
.import-progress-mask.is-quick .import-progress-steps li.active i {
  background: #22d3ee;
  box-shadow: 0 0 14px rgba(34, 211, 238, 0.46);
}

.import-progress-mask.is-quick .import-progress-steps li.active span {
  color: #a7f3d0;
}

@keyframes import-progress-flow {
  0% {
    transform: translateX(-100%);
  }
  50% {
    transform: translateX(90%);
  }
  100% {
    transform: translateX(240%);
  }
}

@keyframes import-progress-scan {
  0% {
    transform: translateX(-100%);
  }
  100% {
    transform: translateX(100%);
  }
}

@media (max-width: 640px) {
  .import-progress-panel {
    padding: 18px;
  }

  .import-progress-head {
    display: grid;
  }

  .import-progress-timer {
    text-align: left;
  }

  .import-progress-timer strong {
    font-size: 30px;
  }

  .import-progress-steps {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
