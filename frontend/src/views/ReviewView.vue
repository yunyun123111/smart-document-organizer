<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  approveReview,
  batchApprove,
  batchDeleteDocuments,
  getReviewDetail,
  listReview,
  skipReview,
  type DocumentListItem,
  type ReviewDetail,
} from '@/api'

const items = ref<DocumentListItem[]>([])
const loading = ref(false)
const drawer = ref(false)
const detail = ref<ReviewDetail | null>(null)
const saving = ref(false)

const editableFields = ref<Record<string, string>>({})
const docType = ref('')
const category = ref('')
const customFilename = ref('')
const selectedIds = ref<number[]>([])
const batchSaving = ref(false)

function onSelectionChange(rows: DocumentListItem[]) {
  selectedIds.value = rows.map((r) => r.id)
}

async function batchConfirm() {
  if (selectedIds.value.length === 0) {
    ElMessage.warning('请先勾选要归档的文件')
    return
  }
  try {
    await ElMessageBox.confirm(
      `将按各自的建议分类与文件名，批量确认归档 ${selectedIds.value.length} 个文件？`,
      '批量确认归档',
      { type: 'warning' },
    )
  } catch {
    return
  }
  batchSaving.value = true
  try {
    const res = await batchApprove(selectedIds.value)
    if (res.failed_count === 0) {
      ElMessage.success(`已批量归档 ${res.success_count} 个文件`)
    } else {
      ElMessage.warning(`归档完成 ${res.success_count} 个，失败 ${res.failed_count} 个`)
      const errs = res.results.filter((r) => !r.success).map((r) => r.error || '未知错误')
      ElMessageBox.alert(`失败原因：${errs.join('；')}`, '部分归档失败', { type: 'warning' })
    }
    drawer.value = false
    await load()
  } finally {
    batchSaving.value = false
  }
}

async function batchRemove() {
  if (selectedIds.value.length === 0) {
    ElMessage.warning('请先勾选要移除的文件')
    return
  }
  try {
    await ElMessageBox.confirm(
      `将从系统移除 ${selectedIds.value.length} 个文件（记录和磁盘文件一并删除，不可恢复），确定？`,
      '批量移除',
      { type: 'warning', confirmButtonText: '确认移除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  batchSaving.value = true
  try {
    const res = await batchDeleteDocuments(selectedIds.value)
    ElMessage.success(`已移除 ${res.deleted_count} 个文件`)
    await load()
  } finally {
    batchSaving.value = false
  }
}

async function load() {
  loading.value = true
  try {
    items.value = await listReview()
  } finally {
    loading.value = false
  }
}

async function open(id: number) {
  drawer.value = true
  detail.value = null
  detail.value = await getReviewDetail(id)
  docType.value = detail.value.document.document_type || ''
  category.value = detail.value.suggested_category
  customFilename.value = ''
  editableFields.value = { ...detail.value.fields }
  delete editableFields.value['suggested_category']
}

async function confirmArchive() {
  if (!detail.value) return
  saving.value = true
  try {
    await approveReview(detail.value.document.id, {
      document_type: docType.value || undefined,
      category_path: category.value,
      filename: customFilename.value || undefined,
      fields: editableFields.value,
    })
    ElMessage.success('已归档')
    drawer.value = false
    await load()
  } finally {
    saving.value = false
  }
}

async function skip() {
  if (!detail.value) return
  saving.value = true
  try {
    await ElMessageBox.confirm('跳过该文件？文件将保留在收件箱不归档。', '提示', { type: 'warning' })
    await skipReview(detail.value.document.id)
    ElMessage.success('已跳过')
    drawer.value = false
    await load()
  } catch (e: any) {
    if (e !== 'cancel') throw e
  } finally {
    saving.value = false
  }
}

function fmtSize(n: number): string {
  if (n > 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB'
  if (n > 1024) return (n / 1024).toFixed(1) + ' KB'
  return n + ' B'
}

onMounted(load)
</script>

<template>
  <div>
    <el-card shadow="never">
      <template #header>
        <div class="head">
          <span>待人工确认（{{ items.length }}）</span>
          <div>
            <el-button
              type="success"
              size="small"
              :disabled="selectedIds.length === 0"
              :loading="batchSaving"
              @click="batchConfirm"
            >批量确认归档（{{ selectedIds.length }}）</el-button>
            <el-button
              type="danger"
              size="small"
              :disabled="selectedIds.length === 0"
              :loading="batchSaving"
              @click="batchRemove"
            >批量移除（{{ selectedIds.length }}）</el-button>
            <el-button size="small" @click="load">刷新</el-button>
          </div>
        </div>
      </template>

      <el-empty v-if="!loading && items.length === 0" description="暂无待确认文件" />

      <el-table :data="items" v-loading="loading" style="width: 100%" @selection-change="onSelectionChange">
        <el-table-column type="selection" width="45" />
        <el-table-column prop="original_filename" label="文件名" min-width="200" show-overflow-tooltip />
        <el-table-column prop="document_type" label="识别类型" width="140">
          <template #default="{ row }">
            <el-tag v-if="row.document_type">{{ row.document_type }}</el-tag>
            <el-tag v-else type="info">未识别</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="file_type" label="类型" width="80" />
        <el-table-column prop="file_size" label="大小" width="100">
          <template #default="{ row }">{{ fmtSize(row.file_size) }}</template>
        </el-table-column>
        <el-table-column label="置信度" width="120">
          <template #default="{ row }">
            <span v-if="row.confidence !== null">{{ (row.confidence * 100).toFixed(0) }}%</span>
            <span v-else class="gray">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间" width="170" />
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link @click="open(row.id)">审核</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 审核抽屉 -->
    <el-drawer v-model="drawer" title="人工审核" size="560px">
      <div v-if="detail" v-loading="saving">
        <el-descriptions :column="1" border class="mb16">
          <el-descriptions-item label="原文件名">{{ detail.document.original_filename }}</el-descriptions-item>
          <el-descriptions-item label="大小">{{ fmtSize(detail.document.file_size) }}</el-descriptions-item>
          <el-descriptions-item label="识别置信度">
            {{ detail.document.confidence !== null ? (detail.document.confidence * 100).toFixed(0) + '%' : '—' }}
          </el-descriptions-item>
        </el-descriptions>

        <div class="field-title">文档类型</div>
        <el-input v-model="docType" placeholder="如：销售合同" class="mb16" />

        <div class="field-title">归档分类</div>
        <el-select v-model="category" filterable allow-create class="mb16 w100">
          <el-option v-for="c in detail.categories" :key="c" :label="c" :value="c" />
        </el-select>

        <div class="field-title">识别字段（可修改）</div>
        <div class="fields-grid">
          <div v-for="k in Object.keys(editableFields)" :key="k" class="field-row">
            <span class="field-key">{{ k }}</span>
            <el-input v-model="editableFields[k]" size="small" />
          </div>
        </div>

        <div class="field-title">建议文件名</div>
        <el-input v-model="customFilename" :placeholder="detail.suggested_filename" class="mb16" />
        <div class="gray small">留空则按分类模板自动生成：{{ detail.templates.category }}</div>

        <div class="actions">
          <el-button type="success" @click="confirmArchive">✓ 确认归档</el-button>
          <el-button @click="skip">跳过</el-button>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.mb16 { margin-bottom: 16px; }
.w100 { width: 100%; }
.field-title {
  font-size: 13px;
  font-weight: 600;
  color: #606266;
  margin: 12px 0 6px;
}
.fields-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
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
.actions {
  margin-top: 24px;
  display: flex;
  gap: 12px;
}
.gray { color: #909399; }
.small { font-size: 12px; }
</style>
