<script setup lang="ts">
/**
 * 业务档案（V2.0）
 * - 左侧：档案列表 + 筛选（状态 / 合同号、船名关键词）
 * - 右侧：全景档案详情（关键字段 + 单据清单），点击单据内嵌预览原件
 * - 支持：新建档案、关联已有文档、解绑、状态流转、回溯归集
 */
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useMobile } from '@/composables/useMobile'
import DocumentPreview from '@/components/DocumentPreview.vue'
import {
  addBusinessFile,
  backfillBusinessArchives,
  createBusinessArchive,
  getBusinessArchive,
  listBusinessArchives,
  listDocuments,
  removeBusinessFile,
  updateBusinessArchive,
  type BusinessArchiveCreate,
  type BusinessFileOut,
  type BusinessRecordOut,
  type DocumentListItem,
} from '@/api'

// ---------------- 列表与筛选 ----------------
const items = ref<BusinessRecordOut[]>([])
const total = ref(0)
const loading = ref(false)
const statusFilter = ref('')
const keyword = ref('')
const page = ref(1)
const pageSize = ref(20)
const current = ref<BusinessRecordOut | null>(null)
const { isMobile } = useMobile()

const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '进行中', value: 'active' },
  { label: '已完成', value: 'completed' },
  { label: '已归档', value: 'archived' },
]

const statusLabels: Record<string, string> = {
  active: '进行中',
  completed: '已完成',
  archived: '已归档',
}

const statusTagTypes: Record<string, 'primary' | 'success' | 'info' | 'warning' | 'danger'> = {
  active: 'primary',
  completed: 'success',
  archived: 'info',
}

const roleLabels: Record<string, string> = {
  contract: '合同',
  settlement: '结算单',
  invoice: '发票',
  cargo_right: '货权',
  other: '其他',
}

const roleTagTypes: Record<string, 'primary' | 'success' | 'info' | 'warning' | 'danger'> = {
  contract: 'primary',
  settlement: 'warning',
  invoice: 'success',
  cargo_right: 'info',
  other: 'info',
}

const sourceLabels: Record<string, string> = {
  auto_rule: '自动规则',
  excel_ledger: 'Excel台账',
  manual: '手动',
}

function statusLabel(v: string): string {
  return statusLabels[v] || v || '—'
}

function statusTagType(v: string) {
  return statusTagTypes[v] || 'info'
}

function roleLabel(v: string): string {
  return roleLabels[v] || v || '其他'
}

function roleTagType(v: string) {
  return roleTagTypes[v] || 'info'
}

function sourceLabel(v: string): string {
  return sourceLabels[v] || v || '—'
}

interface Completeness {
  expected_roles: string[]
  expected_labels: string[]
  present_roles: string[]
  missing_roles: string[]
  missing_labels: string[]
  percent: number
  complete: boolean
}

function comp(item: BusinessRecordOut | null): Completeness | null {
  if (!item || !item.completeness) return null
  return item.completeness as Completeness
}

function fmtAmount(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtTime(v?: string): string {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

async function loadList() {
  loading.value = true
  try {
    const res = await listBusinessArchives({
      status: statusFilter.value || undefined,
      keyword: keyword.value || undefined,
      skip: (page.value - 1) * pageSize.value,
      limit: pageSize.value,
    })
    items.value = res.items
    total.value = res.total
    // 列表刷新后同步当前选中档案（数据变化时更新详情）
    if (current.value) {
      const hit = res.items.find((x) => x.id === current.value!.id)
      if (hit) {
        current.value = hit
      } else {
        current.value = null
        previewDocId.value = null
      }
    }
  } catch {
    // http 拦截器已提示
  } finally {
    loading.value = false
  }
}

function onSearch() {
  page.value = 1
  loadList()
}

function onPage(p: number) {
  page.value = p
  loadList()
}

async function selectArchive(item: BusinessRecordOut) {
  try {
    const res = await getBusinessArchive(item.id)
    current.value = res.data
    const first = res.data.files[0]
    previewDocId.value = first?.document_id ?? null
    previewFileType.value = first?.file_type || ''
  } catch {
    // 拦截器已提示
  }
}

// ---------------- 预览联动 ----------------
const previewDocId = ref<number | null>(null)
const previewFileType = ref('')

function openPreview(f: BusinessFileOut) {
  previewDocId.value = f.document_id
  previewFileType.value = f.file_type || ''
}

// ---------------- 新建档案 ----------------
const createVisible = ref(false)
const creating = ref(false)
const form = ref<BusinessArchiveCreate>({
  business_no: '',
  title: '',
  business_type: '',
  ship_name: '',
  counterparty: '',
  total_amount: undefined,
  sign_date: '',
})

function resetForm() {
  form.value = {
    business_no: '',
    title: '',
    business_type: '',
    ship_name: '',
    counterparty: '',
    total_amount: undefined,
    sign_date: '',
  }
}

function openCreate() {
  resetForm()
  createVisible.value = true
}

async function doCreate() {
  if (!form.value.business_no.trim()) {
    ElMessage.warning('请填写合同号 / 业务编号')
    return
  }
  creating.value = true
  try {
    const payload: BusinessArchiveCreate = {
      business_no: form.value.business_no.trim(),
      title: form.value.title || undefined,
      business_type: form.value.business_type || undefined,
      ship_name: form.value.ship_name || undefined,
      counterparty: form.value.counterparty || undefined,
      total_amount: form.value.total_amount ?? undefined,
      sign_date: form.value.sign_date || undefined,
      status: 'active',
    }
    const res = await createBusinessArchive(payload)
    ElMessage.success('档案创建成功')
    createVisible.value = false
    await loadList()
    await selectArchive(res.data)
  } catch {
    // 拦截器已提示（如 409 重复编号）
  } finally {
    creating.value = false
  }
}

// ---------------- 关联已有文档 ----------------
const linkVisible = ref(false)
const linking = ref(false)
const docCandidates = ref<DocumentListItem[]>([])
const selectedDocIds = ref<number[]>([])

async function openLinkDialog() {
  if (!current.value) return
  linkVisible.value = true
  selectedDocIds.value = []
  try {
    const docs = await listDocuments({ limit: 200 })
    const linked = new Set(current.value.files.map((f) => f.document_id))
    docCandidates.value = docs.filter((d) => !linked.has(d.id))
  } catch {
    docCandidates.value = []
  }
}

function onDocSelect(rows: DocumentListItem[]) {
  selectedDocIds.value = rows.map((r) => r.id)
}

async function doLink() {
  if (!current.value || selectedDocIds.value.length === 0) return
  linking.value = true
  let okCount = 0
  let failCount = 0
  try {
    for (const docId of selectedDocIds.value) {
      try {
        await addBusinessFile(current.value.id, docId)
        okCount += 1
      } catch {
        failCount += 1
      }
    }
    if (okCount > 0) ElMessage.success(`已关联 ${okCount} 份文档`)
    if (failCount > 0) ElMessage.warning(`${failCount} 份文档关联失败`)
    linkVisible.value = false
    await loadList()
    await selectArchive(current.value)
  } finally {
    linking.value = false
  }
}

// ---------------- 解绑 ----------------
async function unlink(f: BusinessFileOut) {
  if (!current.value) return
  try {
    await ElMessageBox.confirm(`确定解除「${f.document_filename}」与该档案的关联吗？\n（不会删除物理文档）`, '解除关联', {
      type: 'warning',
      confirmButtonText: '解除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await removeBusinessFile(current.value.id, f.document_id)
    ElMessage.success('已解除关联')
    await loadList()
    await selectArchive(current.value)
  } catch {
    // 拦截器已提示
  }
}

// ---------------- 状态流转 ----------------
async function changeStatus(status: string) {
  if (!current.value || status === current.value.status) return
  try {
    await updateBusinessArchive(current.value.id, { status })
    ElMessage.success(`已更新为「${statusLabel(status)}」`)
    await loadList()
    await selectArchive(current.value)
  } catch {
    // 拦截器已提示
  }
}

// ---------------- 回溯归集 ----------------
const backfilling = ref(false)

async function doBackfill() {
  try {
    await ElMessageBox.confirm(
      '回溯归集会扫描全部历史文档，按合同号自动建立/归集业务档案。\n是否继续？',
      '回溯归集',
      { type: 'info', confirmButtonText: '开始归集', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  backfilling.value = true
  try {
    const res = await backfillBusinessArchives()
    const st = res.stats || {}
    ElMessage.success(
      `归集完成：新建档案 ${st.created_records ?? 0}，关联 ${st.linked ?? 0} 份单据，` +
        `无合同号跳过 ${st.skipped_no_contract ?? 0}，失败 ${st.failed ?? 0}`,
    )
    await loadList()
  } catch {
    // 拦截器已提示
  } finally {
    backfilling.value = false
  }
}

onMounted(loadList)
</script>

<template>
  <div class="ba-page">
    <!-- ============ 左：档案列表 ============ -->
    <aside class="ba-side">
      <div class="ba-toolbar">
        <el-input
          v-model="keyword"
          placeholder="搜索合同号 / 船名 / 对方单位"
          clearable
          size="small"
          @keyup.enter="onSearch"
          @clear="onSearch"
        />
        <el-select v-model="statusFilter" size="small" class="ba-status-select" @change="onSearch">
          <el-option v-for="s in statusOptions" :key="s.value" :label="s.label" :value="s.value" />
        </el-select>
        <div class="ba-actions">
          <el-button type="primary" size="small" @click="openCreate">新建档案</el-button>
          <el-button size="small" :loading="backfilling" @click="doBackfill">回溯归集</el-button>
        </div>
      </div>

      <div v-loading="loading" class="ba-list">
        <div
          v-for="item in items"
          :key="item.id"
          class="ba-card"
          :class="{ active: current?.id === item.id }"
          @click="selectArchive(item)"
        >
          <div class="ba-card-head">
            <span class="ba-no">{{ item.business_no }}</span>
            <el-tag :type="statusTagType(item.status)" size="small">{{ statusLabel(item.status) }}</el-tag>
          </div>
          <div class="ba-card-title">{{ item.title || '（未命名档案）' }}</div>
          <div class="ba-card-meta">
            <span class="ba-ship">🚢 {{ item.ship_name || '—' }}</span>
            <span class="ba-count">已归集 {{ item.file_count }} 份单据</span>
          </div>
          <div v-if="comp(item)" class="ba-card-comp">
            <el-tag v-if="comp(item)!.complete" size="small" type="success">单据齐全</el-tag>
            <el-tag v-else size="small" type="warning">还缺：{{ comp(item)!.missing_labels.join('、') }}</el-tag>
          </div>
        </div>
        <el-empty v-if="!loading && items.length === 0" description="暂无业务档案" :image-size="70" />
      </div>

      <div class="ba-pager">
        <el-pagination
          small
          layout="prev, pager, next"
          :total="total"
          :page-size="pageSize"
          :current-page="page"
          @current-change="onPage"
        />
      </div>
    </aside>

    <!-- ============ 右：档案详情 ============ -->
    <main class="ba-main">
      <template v-if="current">
        <div class="ba-detail-head">
          <div class="ba-detail-title">
            <h3>{{ current.business_no }}</h3>
            <el-tag :type="statusTagType(current.status)">{{ statusLabel(current.status) }}</el-tag>
            <el-tag type="info" size="small">已归集 {{ current.file_count }} 份单据</el-tag>
            <el-tag v-if="comp(current)" :type="comp(current)!.complete ? 'success' : 'warning'" size="small">
              {{ comp(current)!.complete ? '单据齐全' : '完整度 ' + comp(current)!.percent + '%' }}
            </el-tag>
          </div>
          <div class="ba-detail-ops">
            <el-button size="small" type="primary" plain @click="openLinkDialog">关联已有文档</el-button>
            <el-dropdown @command="changeStatus">
              <el-button size="small">
                状态操作<el-icon class="el-icon--right"><arrow-down /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="active">设为进行中</el-dropdown-item>
                  <el-dropdown-item command="completed">标记已完成</el-dropdown-item>
                  <el-dropdown-item command="archived">归档档案</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </div>

        <!-- 关键字段 -->
        <el-descriptions :column="isMobile ? 1 : 4" border size="small" class="ba-desc">
          <el-descriptions-item label="合同号">{{ current.business_no }}</el-descriptions-item>
          <el-descriptions-item label="档案名称">{{ current.title || '—' }}</el-descriptions-item>
          <el-descriptions-item label="业务类型">{{ current.business_type || '—' }}</el-descriptions-item>
          <el-descriptions-item label="船名">{{ current.ship_name || '—' }}</el-descriptions-item>
          <el-descriptions-item label="对方单位">{{ current.counterparty || '—' }}</el-descriptions-item>
          <el-descriptions-item label="金额（元）">{{ fmtAmount(current.total_amount) }}</el-descriptions-item>
          <el-descriptions-item label="签订日期">{{ current.sign_date || '—' }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ fmtTime(current.created_at) }}</el-descriptions-item>
        </el-descriptions>

        <!-- 单据完整性 -->
        <div v-if="comp(current)" class="ba-comp-block">
          <div class="ba-comp-head">
            <span class="ba-comp-title">单据完整性</span>
            <span class="ba-comp-percent">{{ comp(current)!.percent }}%</span>
          </div>
          <div class="ba-comp-roles">
            <div
              v-for="(label, i) in comp(current)!.expected_labels"
              :key="i"
              class="ba-comp-role"
              :class="{ done: comp(current)!.present_roles.includes(comp(current)!.expected_roles[i]) }"
            >
              <el-icon class="ba-comp-icon">
                <check v-if="comp(current)!.present_roles.includes(comp(current)!.expected_roles[i])" />
                <close v-else />
              </el-icon>
              <span>{{ label }}</span>
            </div>
          </div>
        </div>

        <!-- 单据清单 + 预览 -->
        <div class="ba-body" :class="{ 'ba-body-mobile': isMobile }">
          <div class="ba-files">
            <h4>单据清单</h4>
            <div v-if="current.files.length === 0" class="ba-no-files">
              <el-empty description="暂无关联单据，点击右上角「关联已有文档」" :image-size="60" />
            </div>
            <div
              v-for="f in current.files"
              :key="f.id"
              class="ba-file-card"
              :class="{ active: previewDocId === f.document_id }"
              @click="openPreview(f)"
            >
              <div class="ba-file-top">
                <el-tag :type="roleTagType(f.file_role)" size="small">{{ roleLabel(f.file_role) }}</el-tag>
                <span class="ba-file-src">{{ sourceLabel(f.link_source) }}</span>
              </div>
              <div class="ba-file-name" :title="f.document_filename">{{ f.document_filename }}</div>
              <div class="ba-file-meta">
                <span>{{ fmtTime(f.created_at) }}</span>
                <el-button link type="danger" size="small" @click.stop="unlink(f)">解绑</el-button>
              </div>
            </div>
          </div>

          <div class="ba-preview">
            <template v-if="previewDocId !== null">
              <div class="ba-preview-bar">
                <span class="ba-preview-name">
                  {{ current.files.find((f) => f.document_id === previewDocId)?.document_filename || '文档预览' }}
                </span>
              </div>
              <DocumentPreview
                :doc-id="previewDocId"
                :file-type="previewFileType"
              />
            </template>
            <el-empty v-else description="点击左侧单据即可预览原件" :image-size="80" />
          </div>
        </div>
      </template>

      <div v-else class="ba-placeholder">
        <el-empty description="从左侧选择业务档案查看全景详情" />
      </div>
    </main>

    <!-- ============ 新建档案 ============ -->
    <el-dialog v-model="createVisible" title="新建业务档案" width="520px" :append-to-body="true">
      <el-form :model="form" label-width="90px">
        <el-form-item label="合同号" required>
          <el-input v-model="form.business_no" placeholder="如 SJWLXS（DD）-2026-YC0452" />
        </el-form-item>
        <el-form-item label="档案名称">
          <el-input v-model="form.title" placeholder="如：托克粉 5000 吨销售业务" />
        </el-form-item>
        <el-form-item label="船名">
          <el-input v-model="form.ship_name" />
        </el-form-item>
        <el-form-item label="对方单位">
          <el-input v-model="form.counterparty" />
        </el-form-item>
        <el-form-item label="金额">
          <el-input-number v-model="form.total_amount" :precision="2" :controls="false" style="width: 100%" placeholder="元" />
        </el-form-item>
        <el-form-item label="签订日期">
          <el-date-picker v-model="form.sign_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
        <el-form-item label="业务类型">
          <el-input v-model="form.business_type" placeholder="如：销售 / 采购" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="doCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- ============ 关联已有文档 ============ -->
    <el-dialog v-model="linkVisible" title="关联已有文档" width="780px" :append-to-body="true">
      <el-table :data="docCandidates" height="380" @selection-change="onDocSelect">
        <el-table-column type="selection" width="45" />
        <el-table-column prop="original_filename" label="文件名" min-width="240" show-overflow-tooltip />
        <el-table-column prop="document_type" label="类型" width="110" />
        <el-table-column prop="status" label="状态" width="100" />
        <el-table-column label="创建时间" width="160">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="linkVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="linking"
          :disabled="selectedDocIds.length === 0"
          @click="doLink"
        >
          关联（{{ selectedDocIds.length }}）
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.ba-page {
  display: flex;
  height: 100%;
  gap: 12px;
  padding: 12px;
  box-sizing: border-box;
  background: #f5f6f8;
}

/* ---------- 左栏 ---------- */
.ba-side {
  width: 340px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 8px;
  border: 1px solid #ebeef5;
  overflow: hidden;
}

.ba-toolbar {
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  border-bottom: 1px solid #f0f2f5;
}

.ba-status-select {
  width: 100%;
}

.ba-actions {
  display: flex;
  gap: 8px;
}

.ba-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
  min-height: 0;
}

.ba-card {
  padding: 10px 12px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  margin-bottom: 8px;
  cursor: pointer;
  transition: all 0.15s;
}

.ba-card:hover {
  border-color: #409eff;
  box-shadow: 0 2px 8px rgba(64, 158, 255, 0.12);
}

.ba-card.active {
  border-color: #409eff;
  background: #ecf5ff;
}

.ba-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.ba-no {
  font-weight: 600;
  font-size: 13px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ba-card-title {
  margin-top: 6px;
  font-size: 12px;
  color: #909399;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ba-card-meta {
  margin-top: 6px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: #606266;
}

.ba-count {
  color: #409eff;
  font-weight: 500;
}

.ba-pager {
  padding: 8px;
  border-top: 1px solid #f0f2f5;
  display: flex;
  justify-content: center;
}

/* ---------- 右栏 ---------- */
.ba-main {
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

.ba-detail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.ba-detail-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.ba-detail-title h3 {
  margin: 0;
  font-size: 17px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ba-detail-ops {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.ba-desc {
  margin-top: 12px;
}

/* 单据完整性 */
.ba-comp-block {
  margin-top: 12px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 10px 12px;
  background: #fafbfc;
  flex-shrink: 0;
}

.ba-comp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.ba-comp-title {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
}

.ba-comp-percent {
  font-size: 13px;
  font-weight: 600;
  color: #409eff;
}

.ba-comp-roles {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.ba-comp-role {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border-radius: 6px;
  font-size: 12px;
  background: #f56c6c1a;
  color: #f56c6c;
  border: 1px solid #f56c6c40;
}

.ba-comp-role.done {
  background: #67c23a1a;
  color: #67c23a;
  border-color: #67c23a40;
}

.ba-comp-icon {
  font-size: 12px;
}

/* 卡片完整度 */
.ba-card-comp {
  margin-top: 6px;
}

.ba-body {
  flex: 1;
  min-height: 0;
  margin-top: 12px;
  display: flex;
  gap: 12px;
}

/* 单据清单 */
.ba-files {
  width: 42%;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  overflow: hidden;
  min-height: 0;
}

.ba-files h4 {
  margin: 0;
  padding: 10px 12px;
  font-size: 13px;
  color: #303133;
  border-bottom: 1px solid #f0f2f5;
  background: #fafbfc;
}

.ba-files .ba-file-card {
  margin: 8px 8px 0;
}

.ba-file-card {
  padding: 10px 12px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s;
  margin-bottom: 8px;
}

.ba-file-card:hover {
  border-color: #409eff;
}

.ba-file-card.active {
  border-color: #409eff;
  background: #ecf5ff;
}

.ba-file-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.ba-file-src {
  font-size: 11px;
  color: #c0c4cc;
}

.ba-file-name {
  margin-top: 6px;
  font-size: 13px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ba-file-meta {
  margin-top: 6px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11px;
  color: #909399;
}

.ba-no-files {
  padding: 8px;
}

/* 预览区 */
.ba-preview {
  flex: 1;
  min-width: 0;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  background: #f5f6f8;
}

.ba-preview-bar {
  padding: 8px 12px;
  background: #fafbfc;
  border-bottom: 1px solid #f0f2f5;
  flex-shrink: 0;
}

.ba-preview-name {
  font-size: 12px;
  color: #606266;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: block;
}

.ba-preview :deep(.doc-preview) {
  flex: 1;
}

.ba-placeholder {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ---------- 移动端 ---------- */
@media (max-width: 768px) {
  .ba-page {
    flex-direction: column;
    height: auto;
    overflow-y: auto;
  }

  .ba-side {
    width: 100%;
    max-height: 45vh;
  }

  .ba-main {
    min-height: 60vh;
  }

  .ba-body {
    flex-direction: column;
  }

  .ba-files {
    width: 100%;
    max-height: 300px;
  }

  .ba-preview {
    height: 55vh;
  }
}
</style>
