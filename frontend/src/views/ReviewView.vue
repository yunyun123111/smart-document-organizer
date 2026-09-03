<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  approveReview,
  batchApprove,
  batchDeleteDocuments,
  getReviewDetail,
  listReviewGroups,
  skipReview,
  type DocumentListItem,
  type ReviewDetail,
  type ReviewGroup,
} from '@/api'
import DocumentPreview from '@/components/DocumentPreview.vue'

const groups = ref<ReviewGroup[]>([])
const loading = ref(false)
const drawer = ref(false)
const detail = ref<ReviewDetail | null>(null)
const saving = ref(false)
const groupSelected = ref<Record<string, number[]>>({})
const groupBusy = ref<string | null>(null)

const editableFields = ref<Record<string, string>>({})
const docType = ref('')
const category = ref('')
const customFilename = ref('')

const totalCount = computed(() => groups.value.reduce((n, g) => n + g.documents.length, 0))

function selCount(key: string): number {
  return (groupSelected.value[key] || []).length
}

function onGroupSelection(key: string, rows: DocumentListItem[]) {
  groupSelected.value[key] = rows.map((r) => r.id)
}

function toggleAllInGroup(g: ReviewGroup) {
  const all = g.documents.map((d) => d.id)
  const cur = groupSelected.value[g.group_key] || []
  groupSelected.value[g.group_key] = cur.length === all.length && all.length > 0 ? [] : [...all]
}

async function groupApprove(g: ReviewGroup) {
  const ids = groupSelected.value[g.group_key] || []
  if (ids.length === 0) {
    ElMessage.warning('请先勾选要归档的文件')
    return
  }
  try {
    await ElMessageBox.confirm(
      `将按各自建议分类与文件名，批量确认归档「${g.group_label}」的 ${ids.length} 个文件？`,
      '批量确认归档',
      { type: 'warning' },
    )
  } catch {
    return
  }
  groupBusy.value = g.group_key
  try {
    const res = await batchApprove(ids)
    if (res.failed_count === 0) {
      ElMessage.success(`已归档 ${res.success_count} 个文件`)
    } else {
      ElMessage.warning(`归档成功 ${res.success_count} 个，失败 ${res.failed_count} 个`)
      const errs = res.results.filter((r) => !r.success).map((r) => r.error || '未知错误')
      ElMessageBox.alert(`失败原因：${errs.join('；')}`, '部分归档失败', { type: 'warning' })
    }
    await load()
  } finally {
    groupBusy.value = null
  }
}

async function groupRemove(g: ReviewGroup) {
  const ids = groupSelected.value[g.group_key] || []
  if (ids.length === 0) {
    ElMessage.warning('请先勾选要移除的文件')
    return
  }
  try {
    await ElMessageBox.confirm(
      `将从系统移除「${g.group_label}」的 ${ids.length} 个文件（记录和磁盘文件一并删除，不可恢复），确定？`,
      '批量移除',
      { type: 'warning', confirmButtonText: '确认移除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  groupBusy.value = g.group_key
  try {
    const res = await batchDeleteDocuments(ids)
    ElMessage.success(`已移除 ${res.deleted_count} 个文件`)
    await load()
  } finally {
    groupBusy.value = null
  }
}

async function load() {
  loading.value = true
  try {
    groups.value = await listReviewGroups()
    // 清理已消失文档的勾选
    const alive = new Set(groups.value.flatMap((g) => g.documents.map((d) => d.id)))
    for (const k of Object.keys(groupSelected.value)) {
      groupSelected.value[k] = (groupSelected.value[k] || []).filter((id) => alive.has(id))
    }
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
          <span>待人工确认（{{ totalCount }}）</span>
          <el-button size="small" @click="load">刷新</el-button>
        </div>
      </template>

      <div v-if="!loading && groups.length === 0">
        <el-empty description="暂无待确认文件" />
      </div>

      <div v-loading="loading" class="groups">
        <div v-for="g in groups" :key="g.group_key" class="group-card">
          <div class="group-head">
            <div class="group-title">
              <span class="group-label">{{ g.group_label }}</span>
              <el-tag size="small" :type="g.group_key.startsWith('single') ? 'info' : 'warning'">
                {{ g.documents.length }} 份
              </el-tag>
              <span v-if="selCount(g.group_key)" class="gray small">已选 {{ selCount(g.group_key) }} 份</span>
            </div>
            <div class="group-actions">
              <el-button size="small" @click="toggleAllInGroup(g)">全选本组</el-button>
              <el-button
                type="success"
                size="small"
                :disabled="selCount(g.group_key) === 0"
                :loading="groupBusy === g.group_key"
                @click="groupApprove(g)"
              >确认归档（{{ selCount(g.group_key) }}）</el-button>
              <el-button
                type="danger"
                size="small"
                :disabled="selCount(g.group_key) === 0"
                :loading="groupBusy === g.group_key"
                @click="groupRemove(g)"
              >移除（{{ selCount(g.group_key) }}）</el-button>
            </div>
          </div>
          <el-table
            :data="g.documents"
            style="width: 100%"
            @selection-change="(rows: any) => onGroupSelection(g.group_key, rows)"
          >
            <el-table-column type="selection" width="45" />
            <el-table-column prop="original_filename" label="文件名" min-width="200" show-overflow-tooltip />
            <el-table-column prop="document_type" label="识别类型" width="130">
              <template #default="{ row }">
                <el-tag v-if="row.document_type">{{ row.document_type }}</el-tag>
                <el-tag v-else type="info">未识别</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="file_type" label="类型" width="70" />
            <el-table-column prop="file_size" label="大小" width="90">
              <template #default="{ row }">{{ fmtSize(row.file_size) }}</template>
            </el-table-column>
            <el-table-column label="置信度" width="90">
              <template #default="{ row }">
                <span v-if="row.confidence !== null">{{ (row.confidence * 100).toFixed(0) }}%</span>
                <span v-else class="gray">—</span>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="时间" width="160" />
            <el-table-column label="操作" width="90" fixed="right">
              <template #default="{ row }">
                <el-button type="primary" link @click="open(row.id)">审核</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>
    </el-card>

    <!-- 单份审核抽屉：左预览 + 右核对修改 分屏 -->
    <el-drawer v-model="drawer" title="人工审核" size="min(1200px, 96vw)">
      <div v-if="detail" v-loading="saving" class="review-split">
        <div class="review-left">
          <DocumentPreview
            :doc-id="detail.document.id"
            :name="detail.document.current_filename || detail.document.original_filename"
            :file-type="detail.document.file_type"
          />
        </div>
        <div class="review-right">
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
.groups {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.group-card {
  border: 1px solid #ebeef5;
  border-radius: 8px;
  overflow: hidden;
}
.group-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 12px;
  background-color: #f7f8fa;
  border-bottom: 1px solid #ebeef5;
}
.group-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.group-label {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}
.group-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
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
.review-split {
  display: flex;
  gap: 16px;
  height: calc(100vh - 120px);
}
.review-left {
  flex: 1 1 55%;
  min-width: 0;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  overflow: hidden;
  background: #f5f6f8;
}
.review-right {
  flex: 1 1 45%;
  min-width: 0;
  overflow-y: auto;
  padding-right: 4px;
}
@media (max-width: 768px) {
  .review-split {
    flex-direction: column;
    height: auto;
  }
  .review-left {
    flex: none;
    height: 45vh;
  }
  .review-right {
    flex: none;
  }
}
</style>
