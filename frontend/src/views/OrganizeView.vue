<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadFile, UploadFiles } from 'element-plus'
import {
  batchArchiveDocuments,
  batchDeleteDocuments,
  batchReprocessDocuments,
  batchUndoDocuments,
  cancelJob,
  getDocument,
  getJob,
  listDocumentCategories,
  listDocuments,
  listFilenameRules,
  listRules,
  openDocumentFile,
  retryFailed as retryFailedApi,
  simulateRules,
  startProcessing,
  updateDocument,
  uploadDocument,
  type DocumentDetail,
  type DocumentListItem,
  type FilenameRule,
  type ProcessingJob,
  type Rule,
  type SimulateResult,
} from '@/api'
import DocumentPreview from '@/components/DocumentPreview.vue'

const uploading = ref(false)
const running = ref<ProcessingJob | null>(null)
const inboxFiles = ref<any[]>([])
const pollTimer = ref<number | null>(null)
const lastFailed = ref(0)

// ---------- 待整理文件列表 ----------
const pendingFiles = ref<DocumentListItem[]>([])
const pendingLoading = ref(false)
const selectedIds = ref<number[]>([])
const categories = ref<string[]>([])

// ---------- 字段确认弹窗 ----------
const editorOpen = ref(false)
const editorLoading = ref(false)
const editorDoc = ref<DocumentDetail | null>(null)
const editorFields = ref<{ name: string; value: string }[]>([])
const activeField = ref(0)
const editorCategory = ref('')
const fieldInputs = ref<HTMLElement[]>([])

function confColor(c: number | null): string {
  if (c == null) return '#909399'
  if (c >= 0.85) return '#67c23a'
  if (c >= 0.6) return '#e6a23c'
  return '#f56c6c'
}

function confLabel(c: number | null): string {
  if (c == null) return '待识别'
  return `${Math.round(c * 100)}%`
}

function fileIconCls(ft: string): string {
  const t = (ft || '').toLowerCase()
  if (t === 'pdf') return 'pdf'
  if (['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'tif', 'tiff'].includes(t)) return 'img'
  if (t === 'doc' || t === 'docx') return 'doc'
  if (t === 'xls' || t === 'xlsx' || t === 'csv') return 'xls'
  return 'other'
}

async function loadInbox() {
  const [p, f] = await Promise.all([
    listDocuments({ status: 'pending', limit: 200 }),
    listDocuments({ status: 'failed', limit: 200 }),
  ])
  const seen = new Set<number>()
  pendingFiles.value = [...p, ...f].filter((d) => (seen.has(d.id) ? false : (seen.add(d.id), true)))
  inboxFiles.value = p
  // 清理已不存在的选中项
  const ids = new Set(pendingFiles.value.map((d) => d.id))
  selectedIds.value = selectedIds.value.filter((id) => ids.has(id))
}

function toggleSelect(id: number) {
  const i = selectedIds.value.indexOf(id)
  if (i >= 0) selectedIds.value.splice(i, 1)
  else selectedIds.value.push(id)
}

function toggleAll() {
  if (selectedIds.value.length === pendingFiles.value.length) {
    selectedIds.value = []
  } else {
    selectedIds.value = pendingFiles.value.map((d) => d.id)
  }
}

// ---------- 批量操作 ----------
async function batchArchive() {
  if (selectedIds.value.length === 0) return
  const res = await batchArchiveDocuments(selectedIds.value)
  if (res.success_count > 0) ElMessage.success(`已归档 ${res.success_count} 份`)
  if (res.failed_count > 0) {
    const errs = res.results.filter((r) => !r.success).map((r) => `#${r.id}: ${r.error}`).slice(0, 3).join('；')
    ElMessage.warning(`${res.failed_count} 份归档失败（${errs}${res.failed_count > 3 ? '…' : ''}）`)
  }
  selectedIds.value = []
  await loadInbox()
}

// 批量归档到指定分类（批量移动目录）
const moveDialog = ref(false)
const moveTarget = ref('')
const moveLoading = ref(false)

async function openMoveDialog() {
  if (selectedIds.value.length === 0) return
  if (categories.value.length === 0) {
    categories.value = await listDocumentCategories()
  }
  moveTarget.value = ''
  moveDialog.value = true
}

async function confirmMove() {
  if (!moveTarget.value) {
    ElMessage.warning('请选择目标分类')
    return
  }
  moveLoading.value = true
  try {
    const res = await batchArchiveDocuments(selectedIds.value, moveTarget.value)
    if (res.success_count > 0) ElMessage.success(`已归档 ${res.success_count} 份到「${moveTarget.value}」`)
    if (res.failed_count > 0) ElMessage.warning(`${res.failed_count} 份归档失败`)
    moveDialog.value = false
    selectedIds.value = []
    await loadInbox()
  } finally {
    moveLoading.value = false
  }
}

async function batchReprocess() {
  if (selectedIds.value.length === 0) return
  await ElMessageBox.confirm(
    `将对选中的 ${selectedIds.value.length} 份文件重新执行识别（OCR/规则/模板/AI），耗时取决于文件数量。继续？`,
    '批量重跑识别',
    { type: 'warning', confirmButtonText: '开始重跑', cancelButtonText: '取消' },
  )
  const res = await batchReprocessDocuments(selectedIds.value)
  if (res.success_count > 0) ElMessage.success(`已重跑 ${res.success_count} 份`)
  if (res.failed_count > 0) ElMessage.warning(`${res.failed_count} 份重跑失败`)
  await loadInbox()
}

async function batchUndo() {
  if (selectedIds.value.length === 0) return
  await ElMessageBox.confirm(
    `将撤销选中的 ${selectedIds.value.length} 份文件的最近一次操作（归档/移动/重命名），文件回到原位置。继续？`,
    '批量撤销',
    { type: 'warning', confirmButtonText: '撤销', cancelButtonText: '取消' },
  )
  const res = await batchUndoDocuments(selectedIds.value)
  if (res.success_count > 0) ElMessage.success(`已撤销 ${res.success_count} 份`)
  if (res.failed_count > 0) {
    const errs = res.results.filter((r) => !r.success).map((r) => r.error).slice(0, 3).join('；')
    ElMessage.warning(`${res.failed_count} 份撤销失败（${errs}${res.failed_count > 3 ? '…' : ''}）`)
  }
  selectedIds.value = []
  await loadInbox()
}

async function batchRemove() {
  if (selectedIds.value.length === 0) return
  await ElMessageBox.confirm(
    `将选中的 ${selectedIds.value.length} 份文件移入回收站，之后可从回收站恢复。继续？`,
    '移入回收站',
    { type: 'warning', confirmButtonText: '移入回收站', cancelButtonText: '取消' },
  )
  const res = await batchDeleteDocuments(selectedIds.value)
  if (res.deleted_count > 0) ElMessage.success(`已移入回收站 ${res.deleted_count} 份`)
  if (res.missing_count > 0) ElMessage.warning(`${res.missing_count} 份文件已缺失，仅删除记录`)
  selectedIds.value = []
  await loadInbox()
}

// ---------- 字段确认弹窗 ----------
async function openEditor(id: number) {
  editorOpen.value = true
  editorLoading.value = true
  editorDoc.value = null
  try {
    const doc = await getDocument(id)
    editorDoc.value = doc
    const skip = new Set(['suggested_category'])
    editorFields.value = (doc.fields || [])
      .filter((f) => !skip.has(f.field_name))
      .map((f) => ({ name: f.field_name, value: f.field_value || '' }))
    editorCategory.value = (doc.fields || []).find((f) => f.field_name === 'suggested_category')?.field_value || '其他'
    activeField.value = 0
    await nextTick()
    focusField(0)
  } catch {
    ElMessage.error('加载文档详情失败')
    editorOpen.value = false
  } finally {
    editorLoading.value = false
  }
}

function focusField(idx: number) {
  activeField.value = idx
  nextTick(() => {
    fieldInputs.value[idx]?.focus()
  })
}

function onEditorKeydown(e: KeyboardEvent) {
  if (!editorOpen.value || editorLoading.value) return
  const n = editorFields.value.length
  if (e.key === 'ArrowDown' && n > 0) {
    e.preventDefault()
    focusField((activeField.value + 1) % n)
  } else if (e.key === 'ArrowUp' && n > 0) {
    e.preventDefault()
    focusField((activeField.value - 1 + n) % n)
  } else if (e.key === 'Enter') {
    e.preventDefault()
    void saveAndArchive()
  } else if (e.key === 'Escape') {
    editorOpen.value = false
  }
}

async function saveAndArchive() {
  if (!editorDoc.value) return
  const fields: Record<string, string> = {}
  for (const f of editorFields.value) {
    if (f.value !== undefined && f.value !== null) fields[f.name] = String(f.value)
  }
  fields.suggested_category = editorCategory.value || '其他'
  try {
    await updateDocument(editorDoc.value.id, { fields })
    const res = await batchArchiveDocuments([editorDoc.value.id])
    if (res.success_count === 1) {
      ElMessage.success('已保存字段并归档')
      editorOpen.value = false
      await loadInbox()
    } else {
      ElMessage.warning(res.results[0]?.error || '归档失败')
    }
  } catch {
    /* 错误已在拦截器提示 */
  }
}

// 打开原件（带鉴权新标签页）
function openOriginal(id: number) {
  void openDocumentFile(id)
}

// ---------- 上传 / 整理 ----------
async function handleUpload(f: File) {
  uploading.value = true
  try {
    const res = await uploadDocument(f)
    if (res.status === 'duplicate') {
      const src = (res as any).duplicate_of
      const st = (res as any).duplicate_status
      ElMessage.warning(
        src
          ? `「${res.filename}」已在库中（重复）——与「${src}」（${st === 'archived' ? '已归档' : st}）内容相同`
          : `「${res.filename}」已在库中（重复）`,
      )
    } else {
      ElMessage.success(`「${res.filename}」已上传待整理`)
    }
    await loadInbox()
  } finally {
    uploading.value = false
  }
}

function onFilesChange(list: File[]) {
  for (const f of list) handleUpload(f)
}

async function start() {
  if (running.value) return
  try {
    const job = await startProcessing()
    running.value = job
    ElMessage.success('批量整理已开始')
    if (isFinished(job)) {
      finish(job)
      return
    }
    poll()
  } catch {
    /* 错误已在拦截器提示 */
  }
}

function isFinished(job: ProcessingJob): boolean {
  return job.status === 'completed' || job.status === 'cancelled' || job.status === 'failed'
}

async function finish(job: ProcessingJob) {
  stopPoll()
  running.value = null
  lastFailed.value = job.failed_count
  ElMessage.success(
    `整理完成：成功 ${job.success_count} / 待审核 ${job.review_count} / 重复 ${job.duplicate_count} / 失败 ${job.failed_count}`,
  )
  await loadInbox()
}

async function retryFailedFiles() {
  if (running.value) return
  try {
    const job = await retryFailedApi()
    running.value = job
    ElMessage.success(`已开始重试失败文件（${job.total_files} 个）`)
    if (isFinished(job)) {
      finish(job)
      return
    }
    poll()
  } catch {
    /* 错误已在拦截器提示 */
  }
}

async function stop() {
  if (running.value) {
    await cancelJob(running.value.id)
    ElMessage.info('已请求取消')
  }
}

function poll() {
  stopPoll()
  pollTimer.value = window.setInterval(async () => {
    if (!running.value) return
    try {
      const job = await getJob(running.value.id)
      if (!running.value) return
      running.value = job
      if (isFinished(job)) {
        finish(job)
      }
    } catch {
      /* 轮询失败保留当前状态，下一轮继续 */
    }
  }, 1000)
}

function stopPoll() {
  if (pollTimer.value !== null) {
    clearInterval(pollTimer.value)
    pollTimer.value = null
  }
}

function percent(): number {
  if (!running.value || running.value.total_files === 0) return 0
  return Math.round((running.value.processed_files / running.value.total_files) * 100)
}

// ---------- 规则模拟器 ----------
const simOpen = ref(false)
const simLoading = ref(false)
const simRuleType = ref<'filename' | 'keyword'>('filename')
const simRules = ref<(FilenameRule | Rule)[]>([])
const simRuleIds = ref<number[]>([])
const simLimit = ref(200)
const simResult = ref<SimulateResult | null>(null)
const simOnlyWrong = ref(false)

async function openSimulator() {
  simOpen.value = true
  simResult.value = null
  simRuleIds.value = []
  await loadSimRules()
}

async function loadSimRules() {
  try {
    if (simRuleType.value === 'filename') {
      simRules.value = await listFilenameRules()
    } else {
      simRules.value = await listRules()
    }
  } catch {
    simRules.value = []
  }
}

function onSimRuleTypeChange() {
  simRuleIds.value = []
  loadSimRules()
}

function simRuleLabel(r: FilenameRule | Rule): string {
  if (simRuleType.value === 'filename') {
    const f = r as FilenameRule
    return `${f.pattern}${f.category_name ? ' → ' + f.category_name : ''}${f.note ? '（' + f.note + '）' : ''}`
  }
  const k = r as Rule
  return `${k.keyword}${k.match_type !== 'contains' ? ' [' + k.match_type + ']' : ''}`
}

async function runSimulate() {
  simLoading.value = true
  simResult.value = null
  try {
    simResult.value = await simulateRules({
      rule_type: simRuleType.value,
      rule_ids: simRuleIds.value.length > 0 ? simRuleIds.value : undefined,
      limit: simLimit.value,
    })
    if (simResult.value.total_tested === 0) {
      ElMessage.info('没有可测试的历史文档（需要有已识别类型的文档）')
    } else if (simResult.value.matched === 0) {
      ElMessage.warning(`规则未命中任何文件（测试 ${simResult.value.total_tested} 份）`)
    }
  } catch {
    /* 错误已在拦截器提示 */
  } finally {
    simLoading.value = false
  }
}

function simDetailRows() {
  if (!simResult.value) return []
  const rows = simResult.value.details
  if (simOnlyWrong.value) return rows.filter((d) => d.matched && d.correct === false)
  return rows
}

onMounted(() => {
  loadInbox()
  window.addEventListener('keydown', onEditorKeydown)
})
onUnmounted(() => {
  stopPoll()
  window.removeEventListener('keydown', onEditorKeydown)
})
</script>

<template>
  <el-card shadow="never" class="mb16">
    <template #header>
      <span>使用提示</span>
    </template>
    <el-steps direction="vertical" :active="4">
      <el-step title="放入文件" description="把杂乱文件拖入「待整理目录」，或在下方上传" />
      <el-step title="智能整理" description="系统自动识别类型、提取字段、计算置信度" />
      <el-step title="人工确认" description="置信度不足的文件进入「待人工确认」列表，可在本页直接编辑字段确认归档" />
      <el-step title="归档完成" description="确认后按「分类/年/月」自动归档，支持随时撤销" />
    </el-steps>
  </el-card>

  <el-card shadow="never">
    <template #header>
      <div class="card-head">
        <span>批量整理</span>
        <el-button size="small" type="primary" plain @click="openSimulator">
          <el-icon style="margin-right: 4px"><magic-stick /></el-icon>
          规则模拟器
        </el-button>
      </div>
    </template>

    <div class="upload-area">
      <el-upload
        drag
        multiple
        :auto-upload="false"
        :show-file-list="false"
        :on-change="(_f: UploadFile, fl: UploadFiles) => onFilesChange(fl.map((i) => i.raw).filter(Boolean) as File[])"
      >
        <el-icon class="el-icon--upload"><upload-filled /></el-icon>
        <div class="el-upload__text">将文件拖到此处，或<em>点击上传</em></div>
      </el-upload>
    </div>

    <div class="action-bar">
      <el-button type="primary" :loading="!!running" :disabled="!!running" @click="start">
        ▶ 开始整理（收件箱内 {{ inboxFiles.length }} 个文件）
      </el-button>
      <el-button v-if="running" type="danger" plain @click="stop">停止</el-button>
      <el-button v-if="lastFailed > 0 && !running" type="warning" plain @click="retryFailedFiles">
        重试失败（{{ lastFailed }} 个）
      </el-button>
    </div>

    <!-- 任务进度 -->
    <template v-if="running">
      <el-card shadow="hover" class="job-card">
        <div class="job-head">
          <span>任务 #{{ running.id }}</span>
          <el-tag :type="running.status === 'running' ? 'primary' : 'success'">
            {{ running.status === 'running' ? '处理中' : running.status }}
          </el-tag>
        </div>
        <el-progress :percentage="percent()" :status="running.status === 'completed' ? 'success' : undefined" />
        <div class="job-counts">
          <span>已处理 <b>{{ running.processed_files }}/{{ running.total_files }}</b></span>
          <span>成功 <b class="c-green">{{ running.success_count }}</b></span>
          <span>待审核 <b class="c-orange">{{ running.review_count }}</b></span>
          <span>重复 <b class="c-gray">{{ running.duplicate_count }}</b></span>
          <span>失败 <b class="c-red">{{ running.failed_count }}</b></span>
        </div>
      </el-card>
    </template>
  </el-card>

  <!-- ============ 待整理文件列表 ============ -->
  <el-card shadow="never" class="mb16 mt16">
    <template #header>
      <div class="card-head">
        <span>待整理文件（{{ pendingFiles.length }}）</span>
        <el-button size="small" text type="primary" @click="loadInbox">
          <el-icon style="margin-right: 4px"><refresh /></el-icon>
          刷新
        </el-button>
      </div>
    </template>

    <el-empty v-if="!pendingLoading && pendingFiles.length === 0" description="暂无待整理文件" :image-size="80" />

    <template v-else>
      <!-- 批量操作栏 -->
      <div v-if="selectedIds.length > 0" class="batch-bar">
        <el-checkbox
          :model-value="selectedIds.length === pendingFiles.length"
          :indeterminate="selectedIds.length > 0 && selectedIds.length < pendingFiles.length"
          @change="toggleAll"
        >
          全选
        </el-checkbox>
        <span class="batch-count">已选 {{ selectedIds.length }} 份</span>
        <el-button type="success" size="small" @click="batchArchive">批量确认归档</el-button>
        <el-button type="primary" size="small" plain @click="openMoveDialog">归档到指定分类</el-button>
        <el-button size="small" @click="batchReprocess">批量重跑识别</el-button>
        <el-button size="small" @click="batchUndo">批量撤销</el-button>
        <el-button type="danger" size="small" plain @click="batchRemove">移入回收站</el-button>
      </div>

      <!-- 文件卡片网格 -->
      <div class="doc-grid" v-loading="pendingLoading">
        <div
          v-for="doc in pendingFiles"
          :key="doc.id"
          class="doc-card"
          :class="{ selected: selectedIds.includes(doc.id) }"
          @click="toggleSelect(doc.id)"
        >
          <!-- 左侧缩略图 -->
          <div class="doc-thumb">
            <span class="thumb-icon" :class="'ti-' + fileIconCls(doc.file_type)">
              {{ fileIconCls(doc.file_type) === 'img' ? '🖼' : fileIconCls(doc.file_type) === 'pdf' ? '📕' : fileIconCls(doc.file_type) === 'doc' ? '📘' : fileIconCls(doc.file_type) === 'xls' ? '📗' : '📄' }}
            </span>
          </div>
          <!-- 右侧信息 -->
          <div class="doc-info">
            <div class="doc-name" :title="doc.current_filename || doc.original_filename">
              {{ doc.current_filename || doc.original_filename }}
            </div>
            <div class="doc-tags">
              <el-tag v-if="doc.document_type" size="small" type="info">{{ doc.document_type }}</el-tag>
              <el-tag v-else size="small" type="warning">未识别</el-tag>
              <span class="conf" :style="{ color: confColor(doc.confidence) }">
                ● {{ confLabel(doc.confidence) }}
              </span>
            </div>
            <div class="doc-actions" @click.stop>
              <el-button size="small" type="primary" plain @click="openEditor(doc.id)">预览 / 编辑字段</el-button>
              <el-button size="small" text @click="openOriginal(doc.id)">打开原件</el-button>
            </div>
          </div>
        </div>
      </div>
    </template>
  </el-card>

  <!-- ============ 归档到指定分类 ============ -->
  <el-dialog v-model="moveDialog" title="批量归档到指定分类" width="480px" append-to-body>
    <div class="move-body">
      <p class="gray small mb8">将选中的 {{ selectedIds.length }} 份文件归档到：</p>
      <el-select v-model="moveTarget" filterable placeholder="请选择分类" style="width: 100%">
        <el-option v-for="c in categories" :key="c" :label="c" :value="c" />
      </el-select>
    </div>
    <template #footer>
      <el-button @click="moveDialog = false">取消</el-button>
      <el-button type="primary" :loading="moveLoading" @click="confirmMove">确认归档</el-button>
    </template>
  </el-dialog>

  <!-- ============ 字段确认弹窗 ============ -->
  <el-dialog
    v-model="editorOpen"
    title="字段确认"
    width="92%"
    top="3vh"
    append-to-body
    destroy-on-close
    class="editor-dialog"
  >
    <div v-loading="editorLoading" class="editor-body">
      <template v-if="editorDoc">
        <!-- 左：文件预览 -->
        <div class="editor-preview">
          <div class="editor-pane-head">
            <span class="gray small">{{ editorDoc.current_filename || editorDoc.original_filename }}</span>
            <el-button size="small" text type="primary" @click="openOriginal(editorDoc.id)">在新标签页打开原件</el-button>
          </div>
          <div class="preview-frame">
            <DocumentPreview :doc-id="editorDoc.id" :file-type="editorDoc.file_type" />
          </div>
        </div>

        <!-- 右：字段编辑区 -->
        <div class="editor-form">
          <div class="form-row">
            <span class="form-label">文档类型</span>
            <el-input v-model="editorDoc.document_type" placeholder="文档类型" />
          </div>
          <div class="form-row">
            <span class="form-label">归档分类</span>
            <el-select v-model="editorCategory" filterable allow-create placeholder="选择/输入分类" style="width: 100%">
              <el-option v-for="c in categories" :key="c" :label="c" :value="c" />
            </el-select>
          </div>
          <div class="form-hint gray small mb8">
            快捷键：↑ / ↓ 切换字段 · Enter 确认归档 · Esc 关闭
          </div>
          <div v-if="editorFields.length === 0" class="gray small">该文档未提取到业务字段</div>
          <div
            v-for="(f, i) in editorFields"
            :key="f.name"
            class="form-row field-row"
            :class="{ active: activeField === i }"
            @click="focusField(i)"
          >
            <span class="form-label field-label" :title="f.name">{{ f.name }}</span>
            <el-input
              :ref="(el: any) => (fieldInputs[i] = el as HTMLElement)"
              v-model="f.value"
              :placeholder="f.name"
              @keydown.enter.prevent="saveAndArchive"
            />
          </div>
          <div class="form-actions">
            <el-button type="success" @click="saveAndArchive">✓ 保存并归档</el-button>
            <el-button @click="editorOpen = false">取消</el-button>
          </div>
        </div>
      </template>
    </div>
  </el-dialog>

  <!-- ============ 规则模拟器弹窗 ============ -->
  <el-dialog v-model="simOpen" title="规则模拟器" width="78%" top="4vh" destroy-on-close>
    <div class="sim-body">
      <div class="sim-config">
        <el-radio-group v-model="simRuleType" @change="onSimRuleTypeChange">
          <el-radio-button value="filename">文件名规则</el-radio-button>
          <el-radio-button value="keyword">分类关键词规则</el-radio-button>
        </el-radio-group>

        <div class="sim-config-row">
          <span class="sim-config-label">规则范围</span>
          <el-select
            v-model="simRuleIds"
            multiple
            filterable
            collapse-tags
            collapse-tags-tooltip
            clearable
            placeholder="不选 = 全部启用规则"
            style="width: 380px"
          >
            <el-option v-for="r in simRules" :key="r.id" :label="simRuleLabel(r)" :value="r.id" />
          </el-select>
        </div>

        <div class="sim-config-row">
          <span class="sim-config-label">测试数量</span>
          <el-input-number v-model="simLimit" :min="10" :max="1000" :step="50" style="width: 140px" />
          <span class="gray small">份（最近上传的文档优先，含已归档/待审核等所有已识别类型的文件）</span>
        </div>

        <div class="sim-config-row">
          <el-button type="primary" :loading="simLoading" @click="runSimulate">▶ 开始模拟</el-button>
          <span class="gray small">只读测试，不修改任何文件和数据</span>
        </div>
      </div>

      <template v-if="simResult">
        <div class="sim-stats">
          <div class="stat-card">
            <div class="stat-num">{{ simResult.total_tested }}</div>
            <div class="stat-label">测试文件</div>
          </div>
          <div class="stat-card ok">
            <div class="stat-num">{{ simResult.matched }}</div>
            <div class="stat-label">规则命中</div>
          </div>
          <div class="stat-card warn">
            <div class="stat-num">{{ simResult.misclassified }}</div>
            <div class="stat-label">疑似误判</div>
          </div>
          <div class="stat-card miss">
            <div class="stat-num">{{ simResult.unmatched }}</div>
            <div class="stat-label">未命中</div>
          </div>
          <div class="stat-card blue">
            <div class="stat-num">{{ (simResult.hit_accuracy * 100).toFixed(1) }}%</div>
            <div class="stat-label">命中准确率</div>
          </div>
          <div class="stat-card blue">
            <div class="stat-num">{{ (simResult.coverage * 100).toFixed(1) }}%</div>
            <div class="stat-label">覆盖率</div>
          </div>
        </div>
        <div class="sim-desc gray small">
          命中准确率 = 判对 ÷ 命中（规则判断正确的能力）；覆盖率 = 命中 ÷ 总数（规则覆盖范围）。
          当前测试 {{ simResult.tested_rules }} 条规则。
        </div>

        <div class="sim-detail-head">
          <span>判定明细</span>
          <el-checkbox v-model="simOnlyWrong">只看误判</el-checkbox>
        </div>
        <el-table :data="simDetailRows()" size="small" max-height="360" style="width: 100%">
          <el-table-column type="index" width="45" />
          <el-table-column prop="filename" label="文件名" min-width="200" show-overflow-tooltip />
          <el-table-column prop="actual_type" label="真实类型" width="120" />
          <el-table-column prop="predicted_type" label="规则判定" width="120">
            <template #default="{ row }">
              <span v-if="row.matched">{{ row.predicted_type }}</span>
              <span v-else class="gray">— 未命中 —</span>
            </template>
          </el-table-column>
          <el-table-column label="结果" width="90">
            <template #default="{ row }">
              <el-tag v-if="row.correct === true" type="success" size="small">✓ 正确</el-tag>
              <el-tag v-else-if="row.correct === false" type="danger" size="small">✗ 误判</el-tag>
              <el-tag v-else type="info" size="small">未命中</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="keywords" label="命中关键词" min-width="140">
            <template #default="{ row }">
              <el-tag v-for="k in row.keywords" :key="k" size="small" class="kw-tag">{{ k }}</el-tag>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="simDetailRows().length === 0" description="无明细" :image-size="60" />
      </template>
    </div>
  </el-dialog>
</template>

<style scoped>
.mb16 { margin-bottom: 16px; }
.mt16 { margin-top: 16px; }
.upload-area { max-width: 560px; }
.action-bar { margin: 16px 0; }
.job-card { margin-top: 8px; }
.job-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  font-weight: 600;
}
.job-counts {
  display: flex;
  gap: 20px;
  margin-top: 12px;
  font-size: 13px;
  color: #606266;
}
.c-green { color: #67c23a; }
.c-orange { color: #e6a23c; }
.c-gray { color: #909399; }
.c-red { color: #f56c6c; }

.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

/* ---------- 待整理文件卡片 ---------- */
.batch-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  background: #f4f8ff;
  border: 1px solid #d9ecff;
  border-radius: 8px;
  padding: 10px 14px;
  margin-bottom: 14px;
}
.batch-count {
  font-size: 13px;
  color: #409eff;
  font-weight: 600;
  margin-right: 6px;
}

.doc-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 12px;
}
.doc-card {
  display: flex;
  gap: 12px;
  border: 1px solid #ebeef5;
  border-radius: 10px;
  padding: 12px;
  background: #fff;
  cursor: pointer;
  transition: box-shadow 0.2s, border-color 0.2s;
}
.doc-card:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
}
.doc-card.selected {
  border-color: #409eff;
  background: #f5f9ff;
}
.doc-thumb {
  flex-shrink: 0;
  width: 64px;
  height: 64px;
  border-radius: 8px;
  background: #f5f7fa;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid #ebeef5;
}
.thumb-icon {
  font-size: 30px;
  line-height: 1;
}
.doc-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.doc-name {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.doc-tags {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.conf {
  font-size: 12px;
  font-weight: 600;
}
.doc-actions {
  display: flex;
  gap: 4px;
  margin-top: 2px;
}

/* ---------- 字段确认弹窗 ---------- */
.editor-body {
  display: flex;
  gap: 16px;
  min-height: 64vh;
}
.editor-preview {
  flex: 1.2;
  min-width: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid #ebeef5;
  border-radius: 10px;
  overflow: hidden;
}
.editor-pane-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  background: #fafbfc;
  border-bottom: 1px solid #ebeef5;
}
.preview-frame {
  flex: 1;
  min-height: 0;
  overflow: auto;
  background: #f0f2f5;
  padding: 8px;
}
.editor-form {
  flex: 1;
  min-width: 320px;
  overflow-y: auto;
  padding: 4px 4px 4px 8px;
}
.form-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.form-label {
  flex-shrink: 0;
  width: 76px;
  font-size: 13px;
  color: #606266;
  text-align: right;
}
.field-row {
  padding: 6px 8px;
  border-radius: 8px;
  cursor: text;
  border: 1px solid transparent;
}
.field-row:hover {
  background: #fafbfc;
}
.field-row.active {
  border-color: #409eff;
  background: #f5f9ff;
}
.field-label {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.form-actions {
  display: flex;
  gap: 10px;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid #ebeef5;
}
.form-hint {
  margin: 4px 0 10px;
}

/* 模拟器 */
.sim-body { max-height: 78vh; overflow-y: auto; padding-right: 4px; }
.sim-config {
  background: #fafbfc;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 14px;
}
.sim-config-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.sim-config-label {
  font-size: 13px;
  color: #606266;
  flex-shrink: 0;
}
.sim-stats {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 10px;
  margin: 12px 0 6px;
}
.stat-card {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 12px;
  text-align: center;
}
.stat-num { font-size: 22px; font-weight: 700; color: #303133; }
.stat-label { font-size: 12px; color: #909399; margin-top: 4px; }
.stat-card.ok .stat-num { color: #67c23a; }
.stat-card.warn .stat-num { color: #f56c6c; }
.stat-card.miss .stat-num { color: #e6a23c; }
.stat-card.blue .stat-num { color: #409eff; }
.sim-desc { margin-bottom: 10px; }
.sim-detail-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 14px;
  font-weight: 600;
  margin: 12px 0 8px;
  color: #303133;
}
.kw-tag { margin-right: 4px; }
.gray { color: #909399; }
.small { font-size: 12px; }

@media (max-width: 768px) {
  .editor-body {
    flex-direction: column;
  }
  .editor-preview {
    min-height: 40vh;
  }
  .editor-form {
    min-width: 0;
  }
  .doc-grid {
    grid-template-columns: 1fr;
  }
}
</style>
