<script setup lang="ts">
import { ref } from 'vue'
import BugFieldEditor from '../BugFieldEditor.vue'
import type { BugField, SubmittedBug } from '../../types/case'

defineProps<{
  open: boolean
  loading: boolean
  submitting: boolean
  imageUploading: boolean
  error: string
  resultUrl: string | null
  title: string
  description: string
  fields: BugField[]
  hasImage: boolean
  keyImage: string | null
  keyImages: { platform: string; image: string }[]
  excludedImages: string[]
  submittedBugs: SubmittedBug[]
}>()

const emit = defineEmits<{
  close: []
  submit: []
  submitAnother: []
  updateTitle: [value: string]
  updateDescription: [value: string]
  setFieldSelected: [field: BugField, value: string | string[] | null]
  toggleKeyImage: [image: string]
  uploadImages: [files: File[]]
}>()

const fileInputRef = ref<HTMLInputElement | null>(null)
const dropzoneRef = ref<HTMLElement | null>(null)
const imageDragOver = ref(false)

function isKeyImageExcluded(excludedImages: string[], image: string): boolean {
  return excludedImages.includes(image)
}

function selectImages(): void {
  fileInputRef.value?.click()
}

function focusDropzone(): void {
  dropzoneRef.value?.focus()
}

function emitImages(files: FileList | File[]): void {
  const imageFiles = Array.from(files).filter((file) => file.type.startsWith('image/'))
  if (!imageFiles.length) return
  emit('uploadImages', imageFiles)
}

function handleFileChange(event: Event): void {
  const input = event.target as HTMLInputElement
  if (input.files) emitImages(input.files)
  input.value = ''
}

function handlePaste(event: ClipboardEvent): void {
  const imageFiles = Array.from(event.clipboardData?.files ?? []).filter((file) => file.type.startsWith('image/'))
  if (!imageFiles.length) return
  event.preventDefault()
  emit('uploadImages', imageFiles)
}

function handleDragLeave(event: DragEvent): void {
  const current = event.currentTarget as HTMLElement | null
  if (current && event.relatedTarget instanceof Node && current.contains(event.relatedTarget)) {
    return
  }
  imageDragOver.value = false
}

function handleDrop(event: DragEvent): void {
  imageDragOver.value = false
  if (event.dataTransfer?.files) emitImages(event.dataTransfer.files)
}

</script>

<template>
  <div v-if="open" class="modal-mask">
    <section class="import-modal bug-modal" @paste="handlePaste">
      <div class="modal-head">
        <div>
          <h2>提交 bug 到飞书项目</h2>
          <p>已按 case 与诊断预填，确认/修改后提交；提交后图片在后台异步渲染到描述，不用等待。</p>
        </div>
        <button type="button" @click="emit('close')">关闭</button>
      </div>

      <div class="bug-modal-body">
        <div v-if="submittedBugs.length" class="bug-submitted-list">
          <span class="bug-field-label">已提交 {{ submittedBugs.length }} 条 bug（可继续再提一条）</span>
          <a
            v-for="(bug, index) in submittedBugs"
            :key="bug.id || index"
            :href="bug.url"
            target="_blank"
            rel="noopener"
          >
            #{{ index + 1 }} {{ bug.url }}
          </a>
        </div>

        <div v-if="loading" class="repair-loading-card">
          <strong>正在生成 bug 草稿</strong>
          <span>读取 quick case、飞书需求链接、诊断结论与标准模板配置，并预填缺陷字段。</span>
        </div>

        <div v-else-if="resultUrl" class="bug-success">
          <strong>本次已提交（链接见上方列表）</strong>
          <span v-if="hasImage" class="bug-async-note">关键截图正在后台渲染到 bug 描述。</span>
        </div>

        <template v-else>
          <div class="asset-edit-form">
            <label class="wide">
              标题
              <input :value="title" @input="emit('updateTitle', ($event.target as HTMLInputElement).value)" />
            </label>
            <label class="wide">
              描述（可编辑）
              <textarea
                :value="description"
                rows="10"
                @input="emit('updateDescription', ($event.target as HTMLTextAreaElement).value)"
              />
            </label>

            <div
              ref="dropzoneRef"
              class="bug-image-dropzone"
              :class="{ active: imageDragOver, uploading: imageUploading }"
              tabindex="0"
              @click="focusDropzone"
              @dragenter.prevent="imageDragOver = true"
              @dragover.prevent="imageDragOver = true"
              @dragleave="handleDragLeave"
              @drop.prevent="handleDrop"
            >
              <input
                ref="fileInputRef"
                type="file"
                accept="image/*"
                multiple
                hidden
                @change="handleFileChange"
              />
              <strong>补充截图</strong>
              <span>把问题现场、对比图或手动截图放在这里。提交时会和诊断截图一起带上。</span>
              <small>{{ imageUploading ? '上传中…' : '点击此区域后直接粘贴截图，或把图片拖进来' }}</small>
              <button type="button" class="bug-image-upload-button" @click.stop="selectImages">上传图片</button>
            </div>

            <div v-if="keyImages.length" class="bug-keyimage">
              <span class="bug-field-label">待随 bug 提交的截图（诊断图 + 手动补图；叉掉则本次不带）</span>
              <div class="bug-keyimage-grid">
                <div
                  v-for="(image, index) in keyImages"
                  :key="index"
                  class="bug-keyimage-item"
                  :class="{ excluded: isKeyImageExcluded(excludedImages, image.image) }"
                >
                  <a :href="image.image" target="_blank" rel="noopener">
                    <img :src="image.image" :alt="image.platform || '诊断关键截图'" />
                  </a>
                  <button
                    type="button"
                    class="bug-keyimage-x"
                    :title="isKeyImageExcluded(excludedImages, image.image) ? '点击恢复，本次提交带上' : '叉掉，本次提交不带'"
                    @click="emit('toggleKeyImage', image.image)"
                  >
                    {{ isKeyImageExcluded(excludedImages, image.image) ? '↺' : '×' }}
                  </button>
                  <small>{{ image.platform || '证据图' }}{{ isKeyImageExcluded(excludedImages, image.image) ? '（不带）' : '' }}</small>
                </div>
              </div>
            </div>
            <div v-else-if="keyImage" class="bug-keyimage">
              <span class="bug-field-label">诊断关键截图（提交后随描述附上）</span>
              <a :href="keyImage" target="_blank" rel="noopener">
                <img :src="keyImage" alt="诊断关键截图" />
              </a>
            </div>

            <BugFieldEditor
              v-for="field in fields"
              :key="field.fieldKey"
              :field="field"
              @update:selected="emit('setFieldSelected', field, $event)"
            />
          </div>
          <div v-if="error" class="error-state">{{ error }}</div>
        </template>
      </div>

      <div class="modal-actions">
        <template v-if="loading" />
        <template v-else-if="resultUrl">
          <button type="button" @click="emit('submitAnother')">再提一条</button>
          <button type="button" class="primary" @click="emit('close')">完成</button>
        </template>
        <template v-else>
          <button type="button" @click="emit('close')">取消</button>
          <button type="button" class="primary" :disabled="submitting" @click="emit('submit')">
            {{ submitting ? '提交中…' : '提交 bug' }}
          </button>
        </template>
      </div>
    </section>
  </div>
</template>
