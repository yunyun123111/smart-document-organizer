<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useMobile } from '@/composables/useMobile'
import {
  batchDeleteDocuments,
  deleteDocument,
  documentFileUrl,
  exportDocumentsZip,
  getDocument,
  listDocumentCategories,
  listDocuments,
  listDocumentTypes,
  renameDocument,
  type DocumentDetail,
  type DocumentListItem,
} from '@/api'

const items = ref<DocumentListItem[]>([])
const loading = ref(false)
const route = useRoute()
const keyword = ref('')
const status = ref('')
const docType = ref('')
const category = ref('')
const docTypes = ref<string[]>([])
const categories = ref<string[]>([])
const selectedIds = ref<number[]>([])
const batchLoading = ref(false)
const drawer = ref(false)
const detail = ref<DocumentDetail | null>(null)
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
      limit: 200,
    })
  } finally {
    loading.value = false
  }
}

async function loadFilters() {
  const [t, c] = await Promise.all([listDocumentTypes(), listDocumentCategories()])
  docTypes.value = t
  categories.value = c
}

function onSelectionChange(rows: DocumentListItem[]) {
  selectedIds.value = rows.map((r) => r.id)
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
      `确认删除选中的 ${selectedIds.value.length} 个文件？\n将同时删除磁盘文件与记录，不可恢复。`,
      '批量删除',
      { type: 'warning' },
    )
  } catch {
    return
  }
  batchLoading.value = true
  try {
    const res = await batchDeleteDocuments(selectedIds.value)
    ElMessage.success(`已删除 ${res.deleted_count} 个文件`)
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

async function open(id: number) {
  drawer.value = true
  detail.value = await getDocument(id)
}

async function rename(row: DocumentListItem) {
  const base = row.current_filename.replace(/\.[^.]+$/, '')
  try {
    const { value } = await ElMessageBox.prompt('输入新的文件名（不含扩展名）', '重命名', {
      inputValue: base,
      confirmButtonText: '确定',
      cancelButtonText: '取消',
    })
    if (!value || value.trim() === '') return
    const res = await renameDocument(row.id, value.trim())
    ElMessage.success(res.changed ? `已重命名为：${res.filename}` : '文件名未变化')
    await load()
  } catch (e: any) {
    if (e !== 'cancel' && !(e && e === 'cancel')) {
      if (e?.response?.data?.detail) ElMessage.error(e.response.data.detail)
    }
  }
}

async function remove(row: DocumentListItem) {
  try {
    await ElMessageBox.confirm(`确认删除「${row.original_filename}」？`, '删除', { type: 'warning' })
  } catch {
    return
  }
  await deleteDocument(row.id)
  ElMessage.success('已删除')
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

function openFile(id: number) {
  window.open(documentFileUrl(id), '_blank')
}

function fmtSize(n: number): string {
  if (n > 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB'
  if (n > 1024) return (n / 1024).toFixed(1) + ' KB'
  return n + ' B'
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
  <el-card shadow="never">
    <template #header>
      <div class="head">
        <span>文档库（{{ items.length }}）</span>
        <div class="filters">
          <el-select v-model="status" style="width: 120px" @change="load">
            <el-option v-for="o in statusOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
          <el-select v-model="docType" placeholder="按类型筛选" clearable filterable style="width: 150px" @change="load">
            <el-option v-for="t in docTypes" :key="t" :label="t" :value="t" />
          </el-select>
          <el-select v-model="category" placeholder="按分类筛选" clearable filterable style="width: 170px" @change="load">
            <el-option v-for="c in categories" :key="c" :label="c" :value="c" />
          </el-select>
          <el-input v-model="keyword" placeholder="搜索文件名/类型/字段" clearable style="width: 210px" @keyup.enter="load" @clear="load" />
          <el-button type="primary" @click="load">搜索</el-button>
        </div>
      </div>
    </template>

    <div class="batch-bar">
      <el-button
        type="success"
        size="small"
        :disabled="selectedIds.length === 0"
        :loading="batchLoading"
        @click="batchExport"
      >导出压缩包（{{ selectedIds.length }}）</el-button>
      <el-button
        type="danger"
        size="small"
        :disabled="selectedIds.length === 0"
        :loading="batchLoading"
        @click="batchRemove"
      >批量删除（{{ selectedIds.length }}）</el-button>
      <span v-if="selectedIds.length" class="gray small">已选 {{ selectedIds.length }} 项</span>
    </div>

    <!-- 手机端：卡片列表 -->
    <div v-if="isMobile" v-loading="loading" class="mobile-list">
      <div v-for="row in items" :key="row.id" class="doc-card">
        <div class="doc-card-head">
          <el-checkbox
            :model-value="selectedIds.includes(row.id)"
            @change="(v: any) => toggleSelect(row.id, !!v)"
          />
          <span class="doc-name" @click="open(row.id)">{{ row.current_filename }}</span>
        </div>
        <div class="doc-meta">
          <el-tag v-if="row.document_type" size="small" type="primary">{{ row.document_type }}</el-tag>
          <el-tag v-else size="small" type="info">未识别</el-tag>
          <el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          <span v-if="row.confidence !== null" class="gray small">{{ (row.confidence * 100).toFixed(0) }}%</span>
          <span class="gray small">{{ fmtSize(row.file_size) }}</span>
        </div>
        <div class="doc-time gray small">{{ row.created_at }}</div>
        <div class="doc-actions">
          <el-button size="small" @click="rename(row)">重命名</el-button>
          <el-button size="small" @click="open(row.id)">详情</el-button>
          <el-button v-if="row.status === 'archived'" size="small" type="success" @click="openFile(row.id)">打开</el-button>
          <el-button size="small" type="danger" @click="remove(row)">删除</el-button>
        </div>
      </div>
      <el-empty v-if="!loading && items.length === 0" description="暂无文件" />
    </div>

    <!-- 桌面端：表格 -->
    <el-table v-else :data="items" v-loading="loading" style="width: 100%" @selection-change="onSelectionChange">
      <el-table-column type="selection" width="45" />
      <el-table-column prop="current_filename" label="当前文件名" min-width="220" show-overflow-tooltip />
      <el-table-column prop="document_type" label="类型" width="110">
        <template #default="{ row }">
          <el-tag v-if="row.document_type" size="small">{{ row.document_type }}</el-tag>
          <el-tag v-else type="info" size="small">未识别</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="置信度" width="85">
        <template #default="{ row }">
          <span v-if="row.confidence !== null">{{ (row.confidence * 100).toFixed(0) }}%</span>
        </template>
      </el-table-column>
      <el-table-column prop="file_size" label="大小" width="80">
        <template #default="{ row }">{{ fmtSize(row.file_size) }}</template>
      </el-table-column>
      <el-table-column prop="created_at" label="时间" width="160" />
      <el-table-column label="操作" width="230" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="rename(row)">重命名</el-button>
          <el-button link type="primary" @click="open(row.id)">详情</el-button>
          <el-button v-if="row.status === 'archived'" link type="success" @click="openFile(row.id)">
            打开
          </el-button>
          <el-button link type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>

  <el-drawer v-model="drawer" title="文档详情" size="520px">
    <div v-if="detail">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="原始文件名">{{ detail.original_filename }}</el-descriptions-item>
        <el-descriptions-item label="当前文件名">{{ detail.current_filename }}</el-descriptions-item>
        <el-descriptions-item label="当前路径">{{ detail.current_path }}</el-descriptions-item>
        <el-descriptions-item label="文档类型">{{ detail.document_type || '—' }}</el-descriptions-item>
        <el-descriptions-item label="大小">{{ fmtSize(detail.file_size) }}</el-descriptions-item>
        <el-descriptions-item label="SHA256">{{ detail.file_hash }}</el-descriptions-item>
      </el-descriptions>

      <div class="field-title">识别字段</div>
      <el-table :data="detail.fields" size="small" style="width: 100%">
        <el-table-column prop="field_name" label="字段" width="140" />
        <el-table-column prop="field_value" label="值" show-overflow-tooltip />
        <el-table-column prop="source" label="来源" width="80" />
      </el-table>

      <div class="field-title">提取文本</div>
      <el-input type="textarea" :rows="8" readonly :model-value="detail.extracted_text.slice(0, 2000)" />
    </div>
  </el-drawer>
</template>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.filters {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
/* 手机端卡片列表 */
.mobile-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.doc-card {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 12px;
}
.doc-card-head {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.doc-name {
  flex: 1;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  word-break: break-all;
  line-height: 1.4;
}
.doc-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin: 8px 0 4px 24px;
}
.doc-time {
  margin-left: 24px;
}
.doc-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-left: 24px;
  margin-top: 6px;
}
@media (max-width: 768px) {
  .head .filters {
    width: 100%;
  }
  .head .filters .el-select,
  .head .filters .el-input {
    width: 100% !important;
    margin-bottom: 4px;
  }
}
.batch-bar {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 10px;
}
.field-title {
  font-size: 13px;
  font-weight: 600;
  color: #606266;
  margin: 16px 0 6px;
}
.gray { color: #909399; }
.small { font-size: 12px; }
</style>
