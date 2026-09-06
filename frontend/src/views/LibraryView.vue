<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useMobile } from '@/composables/useMobile'
import { useSplitDrag } from '@/composables/useSplitDrag'
import DocumentPreview from '@/components/DocumentPreview.vue'
import {
  batchDeleteDocuments,
  deleteDocument,
  exportDocumentsZip,
  getDocument,
  listDocumentCategories,
  listDocuments,
  listDocumentTypes,
  openDocumentFile,
  suggestDocuments,
  updateDocument,
  type DocumentDetail,
  type DocumentListItem,
  type SuggestItem,
} from '@/api'

const items = ref<DocumentListItem[]>([])
const loading = ref(false)
const route = useRoute()
const keyword = ref('')
const status = ref('')
const docType = ref('')
const category = ref('')
const contractNo = ref('')
const amountMin = ref<number | undefined>(undefined)
const amountMax = ref<number | undefined>(undefined)
const dateRange = ref<[string, string] | null>(null)
const docTypes = ref<string[]>([])

// 搜索建议（自动补全）
async function querySearch(q: string, cb: (items: SuggestItem[]) => void) {
  if (!q || !q.trim()) return cb([])
  try {
    const res = await suggestDocuments(q.trim())
    cb(res.suggestions)
  } catch {
    cb([])
  }
}
const categories = ref<string[]>([])
const selectedIds = ref<number[]>([])
const batchLoading = ref(false)
const { isMobile } = useMobile()

const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '已归档', value: 'archived' },
  { label: '待确认', value: 'need_review' },
  { label: '处理中', value: 'processing' },
  { label: '待处理', value: 'pending' },
  { label: '重复', value: 'duplicate' },
  { label: '已跳过', value: 'skipped' },
  { label: '失败', value: 'failed' },
]

async function load() {
  loading.value = true
  try {
    items.value = await listDocuments({
      keyword: keyword.value || undefined,
      status: status.value || undefined,
      document_type: docType.value || undefined,
      category: category.value || undefined,
      contract_no: contractNo.value || undefined,
      amount_min: amountMin.value,
      amount_max: amountMax.value,
      date_start: dateRange.value ? dateRange.value[0] : undefined,
      date_end: dateRange.value ? dateRange.value[1] : undefined,
      limit: 200,
    })
    // 列表刷新后同步当前选中文档
    if (selectedDoc.value) {
      const hit = items.value.find((x) => x.id === selectedDoc.value!.id)
      if (hit) {
        selectedDoc.value = hit
      } else {
        selectedDoc.value = null
        editDetail.value = null
      }
    }
  } finally {
    loading.value = false
  }
}

async function loadFilters() {
  const [t, c] = await Promise.all([listDocumentTypes(), listDocumentCategories()])
  docTypes.value = t
  categories.value = c
}

function toggleSelect(id: number, checked: boolean) {
  if (checked) {
    if (!selectedIds.value.includes(id)) selectedIds.value.push(id)
  } else {
    selectedIds.value = selectedIds.value.filter((x) => x !== id)
  }
}

async function batchRemove() {
  if (selectedIds.value.length === 0) {
    ElMessage.warning('请先勾选要删除的文件')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确定要删除选中的 ${selectedIds.value.length} 个文件吗？\n文件不会立即永久删除，而是会移动到回收站。你可以之后从回收站恢复。`,
      '批量移入回收站',
      { type: 'warning', confirmButtonText: '移入回收站', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  batchLoading.value = true
  try {
    const res = await batchDeleteDocuments(selectedIds.value)
    ElMessage.success(`已移入回收站 ${res.deleted_count} 个文件`)
    await load()
    await loadFilters()
  } finally {
    batchLoading.value = false
  }
}

async function batchExport() {
  if (selectedIds.value.length === 0) {
    ElMessage.warning('请先勾选要导出的文件')
    return
  }
  batchLoading.value = true
  try {
    await exportDocumentsZip(selectedIds.value, `文档导出_${new Date().toISOString().slice(0, 10)}`)
    ElMessage.success(`已导出 ${selectedIds.value.length} 个文件为压缩包`)
  } finally {
    batchLoading.value = false
  }
}

async function remove(row: DocumentListItem) {
  try {
    await ElMessageBox.confirm(
      `确定要删除「${row.original_filename}」吗？\n文件不会立即永久删除，而是会移动到回收站，之后可以恢复。`,
      '移入回收站',
      { type: 'warning', confirmButtonText: '移入回收站', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await deleteDocument(row.id)
  ElMessage.success('已移入回收站')
  if (selectedDoc.value?.id === row.id) {
    selectedDoc.value = null
    editDetail.value = null
  }
  load()
}

function statusTag(s: string) {
  const map: Record<string, string> = {
    archived: 'success',
    need_review: 'warning',
    pending: 'info',
    processing: 'primary',
    duplicate: 'danger',
    skipped: 'info',
    failed: 'danger',
  }
  return map[s] ?? 'info'
}

function statusLabel(s: string) {
  const map: Record<string, string> = {
    archived: '已归档',
    need_review: '待确认',
    pending: '待处理',
    processing: '处理中',
    duplicate: '重复',
    skipped: '已跳过',
    failed: '失败',
  }
  return map[s] ?? s
}

async function openFile(id: number) {
  try {
    await openDocumentFile(id)
  } catch (e: any) {
    if (e?.response?.data?.detail) ElMessage.error(e.response.data.detail)
  }
}

// ---------------- 全景详情：从左侧选择文档 → 右侧查看（与业务档案一致） ----------------
const selectedDoc = ref<DocumentListItem | null>(null)
const editDetail = ref<DocumentDetail | null>(null)
const editFilename = ref('')
const editDocType = ref('')
const editCategory = ref('')
const editFields = ref<Record<string, string>>({})
const editSaving = ref(false)

const previewExt = computed(() => {
  const name = selectedDoc.value?.current_filename || selectedDoc.value?.original_filename || ''
  return name.toLowerCase().split('.').pop() || ''
})

// 预览区与编辑区可拖拽调宽
const { leftRatio, startDrag } = useSplitDrag()

async function selectDoc(row: DocumentListItem) {
  selectedDoc.value = row
  await loadEdit(row.id)
}

function closeDetail() {
  selectedDoc.value = null
  editDetail.value = null
}

async function loadEdit(id: number) {
  const d = await getDocument(id)
  editDetail.value = d
  editDocType.value = d.document_type || ''
  const f: Record<string, string> = {}
  for (const fld of d.fields) {
    if (fld.field_name === 'suggested_category') continue
    f[fld.field_name] = fld.field_value
  }
  editFields.value = f
  editCategory.value = d.fields.find((x) => x.field_name === 'suggested_category')?.field_value || ''
  editFilename.value = (d.current_filename || '').replace(/\.[^.]+$/, '')
}

async function saveEdit() {
  if (!selectedDoc.value || !editDetail.value) return
  editSaving.value = true
  try {
    await updateDocument(selectedDoc.value.id, {
      document_type: editDocType.value || undefined,
      fields: { ...editFields.value, suggested_category: editCategory.value },
      filename: editFilename.value.trim() || undefined,
    })
    ElMessage.success('已保存')
    await load()
    await selectDoc(selectedDoc.value)
  } catch (e: any) {
    if (e?.response?.data?.detail) ElMessage.error(e.response.data.detail)
  } finally {
    editSaving.value = false
  }
}

function fmtSize(n: number): string {
  if (n > 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB'
  if (n > 1024) return (n / 1024).toFixed(1) + ' KB'
  return n + ' B'
}

function clearFieldFilter() {
  contractNo.value = ''
  amountMin.value = undefined
  amountMax.value = undefined
  dateRange.value = null
  load()
}

function clearAllFilters() {
  keyword.value = ''
  status.value = ''
  docType.value = ''
  category.value = ''
  contractNo.value = ''
  amountMin.value = undefined
  amountMax.value = undefined
  dateRange.value = null
  load()
}

onMounted(async () => {
  // 支持从数据看板等入口带筛选跳转：/library?status=xx&type=xx
  const q = route.query
  if (typeof q.status === 'string') status.value = q.status
  if (typeof q.type === 'string') docType.value = q.type
  if (typeof q.category === 'string') category.value = q.category
  await loadFilters()
  await load()
})
</script>

<template>
  <div class="lib-page">
    <!-- ============ 左：搜索筛选 + 文档列表 ============ -->
    <aside class="lib-side">
      <div class="lib-side-title">
        <span>文档库</span>
        <el-tag size="small" type="info">{{ items.length }} 份</el-tag>
      </div>

      <!-- 搜索筛选板块 -->
      <el-autocomplete
        v-model="keyword"
        :fetch-suggestions="querySearch"
        placeholder="搜索（拼音/错别字/类型/公司/合同号）"
        clearable
        size="small"
        class="lib-search"
        @select="(item: SuggestItem) => { keyword = item.text; load() }"
        @keyup.enter="load"
        @clear="load"
      >
        <template #default="{ item }">
          <span class="suggest-type">{{ item.type }}</span>
          <span>{{ item.text }}</span>
        </template>
      </el-autocomplete>

      <div class="lib-filter-block">
        <div class="lib-filter-label">状态</div>
        <el-select v-model="status" size="small" class="lib-w100" @change="load">
          <el-option v-for="o in statusOptions" :key="o.value" :label="o.label" :value="o.value" />
        </el-select>

        <div class="lib-filter-label">文档类型</div>
        <el-select v-model="docType" placeholder="全部类型" clearable filterable size="small" class="lib-w100" @change="load">
          <el-option v-for="t in docTypes" :key="t" :label="t" :value="t" />
        </el-select>

        <div class="lib-filter-label">归档分类</div>
        <el-select v-model="category" placeholder="全部分类" clearable filterable size="small" class="lib-w100" @change="load">
          <el-option v-for="c in categories" :key="c" :label="c" :value="c" />
        </el-select>

        <div class="lib-filter-label">字段过滤</div>
        <el-popover placement="bottom-start" :width="300" trigger="click">
          <template #reference>
            <el-button size="small" class="lib-w100">高级字段 <el-icon><arrow-down /></el-icon></el-button>
          </template>
          <div class="field-filter">
            <div class="ff-row">
              <span class="ff-label">合同号</span>
              <el-input v-model="contractNo" placeholder="如 XS2026" clearable size="small" @keyup.enter="load" />
            </div>
            <div class="ff-row">
              <span class="ff-label">金额</span>
              <el-input-number v-model="amountMin" :min="0" :controls="false" placeholder="最小" size="small" style="width: 110px" />
              <span class="ff-sep">—</span>
              <el-input-number v-model="amountMax" :min="0" :controls="false" placeholder="最大" size="small" style="width: 110px" />
            </div>
            <div class="ff-row">
              <span class="ff-label">日期</span>
              <el-date-picker
                v-model="dateRange"
                type="daterange"
                value-format="YYYY-MM-DD"
                range-separator="至"
                start-placeholder="开始日期"
                end-placeholder="结束日期"
                size="small"
                style="width: 205px"
              />
            </div>
            <div class="ff-actions">
              <el-button size="small" @click="clearFieldFilter">清空</el-button>
              <el-button size="small" type="primary" @click="load">应用</el-button>
            </div>
          </div>
        </el-popover>
      </div>

      <div class="lib-side-divider" />

      <!-- 批量操作 -->
      <div class="lib-batch">
        <div class="lib-filter-label">批量操作</div>
        <el-button
          type="success"
          size="small"
          class="lib-w100"
          :disabled="selectedIds.length === 0"
          :loading="batchLoading"
          @click="batchExport"
        >导出压缩包（{{ selectedIds.length }}）</el-button>
        <el-button
          type="danger"
          size="small"
          class="lib-w100"
          :disabled="selectedIds.length === 0"
          :loading="batchLoading"
          @click="batchRemove"
        >批量移入回收站（{{ selectedIds.length }}）</el-button>
        <div v-if="selectedIds.length" class="lib-selected-tip">已选 {{ selectedIds.length }} 项</div>
      </div>

      <div class="lib-side-divider" />

      <!-- 文档列表（点击选中 → 右侧全景详情） -->
      <div class="lib-list-label">文档列表</div>
      <div v-loading="loading" class="lib-list">
        <div
          v-for="row in items"
          :key="row.id"
          class="lib-doc-card"
          :class="{ active: selectedDoc?.id === row.id }"
          @click="selectDoc(row)"
        >
          <div class="lib-doc-head">
            <el-checkbox
              :model-value="selectedIds.includes(row.id)"
              @click.stop
              @change="(v: any) => toggleSelect(row.id, !!v)"
            />
            <span class="lib-doc-name" :title="row.current_filename">{{ row.current_filename }}</span>
          </div>
          <div class="lib-doc-meta">
            <el-tag v-if="row.document_type" size="small" type="primary">{{ row.document_type }}</el-tag>
            <el-tag v-else size="small" type="info">未识别</el-tag>
            <el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
            <span v-if="row.confidence !== null" class="gray small">{{ (row.confidence * 100).toFixed(0) }}%</span>
            <span class="gray small">{{ fmtSize(row.file_size) }}</span>
          </div>
          <div class="lib-doc-foot">
            <span class="gray small">{{ row.created_at }}</span>
            <el-button link type="danger" size="small" @click.stop="remove(row)">删除</el-button>
          </div>
        </div>
        <el-empty v-if="!loading && items.length === 0" description="暂无文档" :image-size="60" />
      </div>

      <div class="lib-side-foot">
        <el-button size="small" text type="primary" @click="clearAllFilters">重置全部筛选</el-button>
      </div>
    </aside>

    <!-- ============ 右：全景详情（大预览 + 窄编辑列） ============ -->
    <main class="lib-main">
      <template v-if="selectedDoc && editDetail">
        <div class="lib-detail-head">
          <div class="lib-detail-title">
            <h3 :title="selectedDoc.current_filename">{{ selectedDoc.current_filename }}</h3>
            <el-tag v-if="editDetail.document_type" size="small" type="primary">{{ editDetail.document_type }}</el-tag>
            <el-tag :type="statusTag(editDetail.status)" size="small">{{ statusLabel(editDetail.status) }}</el-tag>
          </div>
          <div class="lib-detail-ops">
            <el-button v-if="editDetail.status === 'archived'" size="small" type="success" plain @click="openFile(editDetail.id)">打开原件</el-button>
            <el-button size="small" type="primary" :loading="editSaving" @click="saveEdit">保存修改</el-button>
            <el-button size="small" @click="closeDetail">关闭</el-button>
          </div>
        </div>

        <div class="lib-body review-split" :class="{ 'lib-body-mobile': isMobile }">
          <!-- 左：原件预览（大，可拖拽调宽） -->
          <div class="lib-preview" :style="{ flexBasis: leftRatio + '%' }">
            <div class="lib-preview-bar">
              <span class="lib-preview-name">原件预览</span>
            </div>
            <DocumentPreview :doc-id="editDetail.id" :name="selectedDoc.current_filename" :file-type="previewExt" />
          </div>

          <div class="splitter" @mousedown="startDrag" />

          <!-- 右：信息核对与修改（窄列滚动） -->
          <div class="lib-info">
            <el-descriptions :column="1" border size="small" class="mb16">
              <el-descriptions-item label="原文件名">{{ editDetail.original_filename }}</el-descriptions-item>
              <el-descriptions-item label="大小">{{ fmtSize(editDetail.file_size) }}</el-descriptions-item>
              <el-descriptions-item label="归档路径">{{ editDetail.current_path }}</el-descriptions-item>
              <el-descriptions-item label="SHA256">{{ editDetail.file_hash }}</el-descriptions-item>
            </el-descriptions>

            <div class="lib-info-block">
              <div class="field-title">文件名（不含扩展名）</div>
              <el-input v-model="editFilename" size="small" placeholder="修改文件名" />
            </div>
            <div class="lib-info-block">
              <div class="field-title">文档类型</div>
              <el-input v-model="editDocType" size="small" placeholder="如：销售合同" />
            </div>
            <div class="lib-info-block">
              <div class="field-title">归档分类</div>
              <el-select v-model="editCategory" filterable allow-create clearable size="small" class="lib-w100">
                <el-option v-for="c in categories" :key="c" :label="c" :value="c" />
              </el-select>
            </div>
            <div class="lib-info-block">
              <div class="field-title">识别字段（可修改）</div>
              <div class="fields-grid">
                <div v-for="k in Object.keys(editFields)" :key="k" class="field-row">
                  <span class="field-key">{{ k }}</span>
                  <el-input v-model="editFields[k]" size="small" />
                </div>
              </div>
              <el-empty v-if="Object.keys(editFields).length === 0" description="暂无识别字段" :image-size="50" />
            </div>
            <div class="lib-info-block">
              <div class="field-title">提取文本</div>
              <el-input type="textarea" :rows="5" readonly :model-value="editDetail.extracted_text.slice(0, 1500)" />
            </div>
          </div>
        </div>
      </template>

      <div v-else class="lib-placeholder">
        <el-empty description="从左侧选择文档查看全景详情" />
      </div>
    </main>
  </div>
</template>

<style scoped>
/* ============ 页面骨架（与业务档案统一：灰底 / 白卡 / 圆角 / 蓝色主色） ============ */
.lib-page {
  display: flex;
  gap: 12px;
  padding: 12px;
  height: 100%;
  box-sizing: border-box;
  background: #f5f6f8;
}

.lib-side {
  width: 300px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 8px;
  border: 1px solid #ebeef5;
  padding: 10px 12px;
  gap: 8px;
  overflow: hidden;
}

.lib-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 8px;
  border: 1px solid #ebeef5;
  padding: 14px;
  overflow: hidden;
}

.lib-side-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 15px;
  font-weight: 600;
  color: #303133;
  padding-bottom: 8px;
  border-bottom: 1px solid #f0f2f5;
  flex-shrink: 0;
}

.lib-search {
  width: 100%;
  flex-shrink: 0;
}

.lib-filter-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex-shrink: 0;
}

.lib-filter-label {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}

.lib-w100 {
  width: 100%;
}

.lib-side-divider {
  height: 1px;
  background: #f0f2f5;
  margin: 2px 0;
  flex-shrink: 0;
}

.lib-batch {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex-shrink: 0;
}

.lib-selected-tip {
  font-size: 12px;
  color: #409eff;
  text-align: center;
}

.lib-list-label {
  font-size: 12px;
  color: #909399;
  flex-shrink: 0;
}

/* 文档列表（点击选中 → 右侧详情） */
.lib-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.lib-doc-card {
  padding: 8px 10px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s;
  flex-shrink: 0;
}

.lib-doc-card:hover {
  border-color: #409eff;
  box-shadow: 0 2px 8px rgba(64, 158, 255, 0.12);
}

.lib-doc-card.active {
  border-color: #409eff;
  background: #ecf5ff;
}

.lib-doc-head {
  display: flex;
  align-items: flex-start;
  gap: 6px;
}

.lib-doc-name {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.4;
}

.lib-doc-meta {
  margin: 6px 0 4px 26px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}

.lib-doc-foot {
  margin-left: 26px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11px;
}

.lib-side-foot {
  text-align: center;
  padding-top: 4px;
  flex-shrink: 0;
}

/* ============ 右侧全景详情 ============ */
.lib-detail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  flex-shrink: 0;
}

.lib-detail-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.lib-detail-title h3 {
  margin: 0;
  font-size: 16px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lib-detail-ops {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.lib-desc {
  margin-top: 12px;
  flex-shrink: 0;
}

.lib-body {
  flex: 1;
  min-height: 0;
  margin-top: 10px;
  display: flex;
  gap: 10px;
}

/* 右：信息核对（窄列滚动，预览优先） */
.lib-info {
  flex: 1 1 auto;
  min-width: 320px;
  max-width: 400px;
  overflow-y: auto;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 12px;
}

.lib-info-block {
  margin-bottom: 12px;
}

/* 左：预览（大，默认占多数，可拖拽） */
.lib-preview {
  flex: 0 0 auto;
  min-width: 0;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  background: #f5f6f8;
}

.lib-preview-bar {
  padding: 8px 12px;
  background: #fafbfc;
  border-bottom: 1px solid #f0f2f5;
  flex-shrink: 0;
}

.lib-preview-name {
  font-size: 12px;
  color: #606266;
}

.lib-preview :deep(.doc-preview) {
  flex: 1;
}

.splitter {
  flex: 0 0 8px;
  cursor: col-resize;
  border-radius: 4px;
  transition: background 0.2s;
  touch-action: none;
}

.splitter:hover {
  background: #409eff40;
}

.lib-placeholder {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ============ 表单小组件 ============ */
.mb16 { margin-bottom: 16px; }
.field-title {
  font-size: 13px;
  font-weight: 600;
  color: #606266;
  margin: 0 0 6px;
}
.fields-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 8px;
}
.field-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.field-key {
  min-width: 90px;
  font-size: 12px;
  color: #909399;
  flex-shrink: 0;
}
.gray { color: #909399; }
.small { font-size: 12px; }

.suggest-type {
  display: inline-block;
  width: 52px;
  color: #909399;
  font-size: 12px;
  margin-right: 8px;
}

/* 字段过滤 popover */
.field-filter .ff-row {
  display: flex;
  align-items: center;
  margin-bottom: 10px;
}
.field-filter .ff-label {
  width: 52px;
  color: #606266;
  font-size: 13px;
  flex-shrink: 0;
}
.field-filter .ff-sep {
  margin: 0 6px;
  color: #909399;
}
.field-filter .ff-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
}

/* ============ 移动端 ============ */
@media (max-width: 768px) {
  .lib-page {
    flex-direction: column;
    height: auto;
    overflow-y: auto;
  }

  .lib-side {
    width: 100%;
    max-height: 48vh;
  }

  .lib-main {
    min-height: 60vh;
  }

  .lib-body {
    flex-direction: column;
  }

  .lib-info {
    min-width: 0;
    max-width: none;
    width: 100%;
    max-height: 320px;
  }

  .lib-preview {
    height: 55vh;
  }

  .splitter {
    display: none;
  }
}
</style>
