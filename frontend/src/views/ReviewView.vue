<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useSplitDrag } from '@/composables/useSplitDrag'
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
const reviewOpen = ref(false)
const detail = ref<ReviewDetail | null>(null)
const saving = ref(false)
const groupSelected = ref<Record<string, number[]>>({})
const groupBusy = ref<string | null>(null)

const editableFields = ref<Record<string, string>>({})
const docType = ref('')
const category = ref('')
const customFilename = ref('')
// 分屏可拖拽调整左右宽度
const { leftRatio, startDrag } = useSplitDrag()

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
      `确定要移除「${g.group_label}」的 ${ids.length} 个文件吗？\n文件不会立即永久删除，而是会移动到回收站，之后可以恢复。`,
      '批量移除',
      { type: 'warning', confirmButtonText: '移入回收站', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  groupBusy.value = g.group_key
  try {
    const res = await batchDeleteDocuments(ids)
    ElMessage.success(`已移入回收站 ${res.deleted_count} 个文件`)
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

function closeReview() {
  reviewOpen.value = false
  detail.value = null
}

async function open(id: number) {
  reviewOpen.value = true
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
    closeReview()
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
    closeReview()
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
  <div class="rv-page">
    <!-- ============ 左：待确认分组列表 ============ -->
    <main class="rv-list">
      <div class="rv-head">
        <div class="rv-title">
          <span>待人工确认</span>
          <el-tag v-if="totalCount" size="small" type="warning">{{ totalCount }} 份</el-tag>
        </div>
        <el-button size="small" @click="load">刷新</el-button>
      </div>

      <div v-if="!loading && groups.length === 0" class="rv-empty">
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
              >移入回收站（{{ selCount(g.group_key) }}）</el-button>
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
    </main>

    <!-- ============ 右：页面内嵌审核分屏（左预览 + 右核对修改） ============ -->
    <aside v-if="reviewOpen" class="rv-review">
      <div class="rv-review-bar">
        <span class="rv-review-name">
          {{ detail?.document.current_filename || detail?.document.original_filename || '人工审核' }}
        </span>
        <div class="rv-review-ops">
          <el-button size="small" @click="closeReview">收起</el-button>
        </div>
      </div>
      <div v-if="detail" v-loading="saving" class="review-split">
        <div class="review-left" :style="{ flexBasis: leftRatio + '%' }">
          <DocumentPreview
            :doc-id="detail.document.id"
            :name="detail.document.current_filename || detail.document.original_filename"
            :file-type="detail.document.file_type"
          />
        </div>
        <div class="splitter" @mousedown="startDrag" />
        <div class="review-right">
          <el-descriptions :column="1" border size="small" class="mb16">
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
          <el-empty v-if="Object.keys(editableFields).length === 0" description="暂无识别字段" :image-size="50" />

          <div class="field-title">建议文件名</div>
          <el-input v-model="customFilename" :placeholder="detail.suggested_filename" class="mb16" />
          <div class="gray small">留空则按分类模板自动生成：{{ detail.templates.category }}</div>

          <div class="actions">
            <el-button type="success" @click="confirmArchive">✓ 确认归档</el-button>
            <el-button @click="skip">跳过</el-button>
          </div>
        </div>
      </div>
    </aside>
  </div>
</template>

<style scoped>
/* ============ 页面骨架（与业务档案统一：灰底 / 白卡 / 圆角 / 蓝色主色） ============ */
.rv-page {
  display: flex;
  gap: 12px;
  padding: 12px;
  height: 100%;
  box-sizing: border-box;
  background: #f5f6f8;
}

.rv-list {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 8px;
  border: 1px solid #ebeef5;
  padding: 12px;
  overflow: hidden;
}

.rv-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding-bottom: 10px;
  border-bottom: 1px solid #f0f2f5;
  flex-shrink: 0;
}

.rv-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

.rv-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.groups {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 12px;
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
  background-color: #fafbfc;
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

/* ============ 右侧审核面板 ============ */
.rv-review {
  width: 58%;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 8px;
  border: 1px solid #ebeef5;
  overflow: hidden;
}

.rv-review-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px;
  background: #fafbfc;
  border-bottom: 1px solid #f0f2f5;
  flex-shrink: 0;
}

.rv-review-name {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.review-split {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 12px;
  padding: 12px;
}

.review-left {
  flex: 0 0 auto;
  min-width: 0;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  overflow: hidden;
  background: #f5f6f8;
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

.review-right {
  flex: 1 1 auto;
  min-width: 0;
  overflow-y: auto;
  padding-right: 4px;
}

/* ============ 表单小组件 ============ */
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
.actions {
  margin-top: 24px;
  display: flex;
  gap: 12px;
}
.gray { color: #909399; }
.small { font-size: 12px; }

/* ============ 移动端 ============ */
@media (max-width: 768px) {
  .rv-page {
    flex-direction: column;
    height: auto;
    overflow-y: auto;
  }

  .rv-list {
    min-height: 45vh;
  }

  .rv-review {
    width: 100%;
    min-height: 70vh;
  }

  .review-split {
    flex-direction: column;
  }

  .review-left {
    flex: none;
    height: 40vh;
  }

  .review-right {
    flex: none;
  }

  .splitter {
    display: none;
  }
}
</style>
