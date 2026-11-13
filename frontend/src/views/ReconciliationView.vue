<script setup lang="ts">
/**
 * 业务线台账自动对账
 * - 顶部：汇总统计 + 一键对账 + 导出 + 定时巡检
 * - 多视图：对账明细 / 客户视角 / 期间视角 / 催办清单 / 外部明细
 * - 右侧抽屉：金额勾稽 + 2×5 单据矩阵 + 差异明细（处理闭环：新增/遗留/已解决）
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useMobile } from '@/composables/useMobile'
import {
  clearReconcileChanges,
  deleteExternalBatch,
  exportReconciliation,
  getReconcileSchedule,
  getReconciliationDetail,
  importExternalDetails,
  listExternalBatches,
  listExternalDetails,
  listInvoiceUniqueness,
  listReconciliation,
  listUrgeItems,
  resolveReconciliationDiff,
  runReconciliation,
  summaryByGroup,
  updateReconcileSchedule,
  urgeReconciliationBusiness,
  type DiffResolutionType,
  type ExternalBatch,
  type ExternalDetail,
  type InvoiceUniquenessItem,
  type ReconcileSchedule,
  type ReconciliationDetail,
  type ReconciliationDiff,
  type ReconciliationMatrixCell,
  type ReconciliationReport,
  type SummaryRow,
  type UrgeItem,
} from '@/api/reconciliation'

// ---------------- 汇总 ----------------
interface Summary {
  total: number
  ok: number
  warning: number
  error: number
  no_data: number
}

const summary = ref<Summary>({ total: 0, ok: 0, warning: 0, error: 0, no_data: 0 })
const summaryLoading = ref(false)

// ---------------- 视图 ----------------
const activeTab = ref('detail')

// ---------------- 列表 ----------------
const items = ref<ReconciliationReport[]>([])
const total = ref(0)
const loading = ref(false)
const running = ref(false)
const exporting = ref(false)
const statusFilter = ref('')
const keyword = ref('')
const page = ref(1)
const pageSize = ref(20)

// ---------------- 详情 ----------------
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<ReconciliationDetail | null>(null)
const { isMobile } = useMobile()
const drawerSize = computed(() => (isMobile.value ? '94%' : '760px'))

const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '有错误', value: 'error' },
  { label: '有警告', value: 'warning' },
  { label: '已对齐', value: 'ok' },
  { label: '未上传资料', value: 'no_data' },
]

const statusMeta: Record<string, { label: string; type: 'success' | 'warning' | 'danger' | 'info' }> = {
  ok: { label: '已对齐', type: 'success' },
  warning: { label: '有警告', type: 'warning' },
  error: { label: '有错误', type: 'danger' },
  no_data: { label: '未上传资料', type: 'info' },
}

const roleLabels: Record<string, string> = {
  contract: '合同',
  settlement: '结算单',
  invoice: '发票',
  cargo_right: '货权单',
  payment_receipt: '收付款回单',
}

const roleOrder = ['contract', 'settlement', 'invoice', 'cargo_right', 'payment_receipt']

const directionLabels: Record<string, string> = {
  up: '上游',
  down: '下游',
}

const diffTypeLabels: Record<string, string> = {
  MISSING_DOC: '缺件',
  AMOUNT_MISMATCH: '金额不符',
  INTEREST_MISMATCH: '利息不符',
  CHAIN_BREAK: '关联断链',
  DATE_CONFLICT: '日期冲突',
  DUPLICATE_BIZ: '重复业务线',
  LEDGER_MISSING: '台账未登记',
  UNPARSED_AMOUNT: '金额无法识别',
  UNCLASSIFIED_DOC: '单据未分类',
}

const severityMeta: Record<string, { label: string; type: 'danger' | 'warning' | 'info' }> = {
  CRITICAL: { label: '严重', type: 'danger' },
  WARNING: { label: '警告', type: 'warning' },
  INFO: { label: '提示', type: 'info' },
}

// 处理方式：前两个关闭差异，其余只记进度
const resolutionOptions: { label: string; value: DiffResolutionType; desc: string }[] = [
  { label: '待补单', value: 'supplement', desc: '资料还没到，记为待补，仍计入差异' },
  { label: '待调账', value: 'adjust', desc: '金额需财务调整，仍计入差异' },
  { label: '待确认', value: 'pending', desc: '需要业务确认，仍计入差异' },
  { label: '误报', value: 'false_positive', desc: '识别/口径误报，关闭该差异' },
  { label: '忽略', value: 'ignore', desc: '确认无需处理，关闭该差异' },
  { label: '撤销处理', value: 'reopen', desc: '清掉此前的处理记录，重新待办' },
]

const resolutionMeta: Record<string, { label: string; type: 'success' | 'warning' | 'info' | 'danger' }> = {
  supplement: { label: '待补单', type: 'warning' },
  adjust: { label: '待调账', type: 'warning' },
  pending: { label: '待确认', type: 'info' },
  false_positive: { label: '误报', type: 'success' },
  ignore: { label: '已忽略', type: 'info' },
}

const hasReports = computed(() => summary.value.total > 0)
const activeDiffCount = computed(() => (detail.value?.diffs || []).filter((d) => !d.ignored).length)

function fmtAmount(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function statusLabel(v: string): string {
  return statusMeta[v]?.label || v || '—'
}

function statusType(v: string) {
  return statusMeta[v]?.type || 'info'
}

function diffTypeLabel(v: string): string {
  return diffTypeLabels[v] || v
}

function severityLabel(v: string): string {
  return severityMeta[v]?.label || v
}

function severityType(v: string) {
  return severityMeta[v]?.type || 'info'
}

function resolutionLabel(v?: string): string {
  return (v && resolutionMeta[v]?.label) || '未处理'
}

function resolutionType(v?: string) {
  return (v && resolutionMeta[v]?.type) || 'info'
}

function cellOf(matrix: ReconciliationMatrixCell[] | undefined, direction: string, role: string) {
  return matrix?.find((c) => c.direction === direction && c.role === role)
}

// ---------------- 加载 ----------------
async function loadList() {
  loading.value = true
  try {
    const res = await listReconciliation({
      status: statusFilter.value || undefined,
      keyword: keyword.value.trim() || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    items.value = res.items
    total.value = res.total
  } catch {
    items.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

async function loadSummary() {
  summaryLoading.value = true
  try {
    const res = await listReconciliation({ page: 1, page_size: 200 })
    const s: Summary = { total: res.total, ok: 0, warning: 0, error: 0, no_data: 0 }
    for (const row of res.items) {
      if (row.status === 'ok') s.ok += 1
      else if (row.status === 'warning') s.warning += 1
      else if (row.status === 'no_data') s.no_data += 1
      else s.error += 1
    }
    summary.value = s
  } catch {
    // 未跑过对账时接口也可能正常返回空列表，这里保持零值
  } finally {
    summaryLoading.value = false
  }
}

async function refresh() {
  await Promise.all([loadList(), loadSummary()])
}

function onFilterChange() {
  page.value = 1
  loadList()
}

function onSearch() {
  page.value = 1
  loadList()
}

async function runAll() {
  running.value = true
  try {
    const res = await runReconciliation({})
    ElMessage.success(
      `对账完成：${res.total} 条业务线，正常 ${res.ok} / 警告 ${res.warning} / 错误 ${res.error}，耗时 ${res.duration_ms}ms`,
    )
    page.value = 1
    await refresh()
    await loadSchedule()
  } finally {
    running.value = false
  }
}

async function doExport() {
  exporting.value = true
  try {
    const blob = await exportReconciliation({
      status: statusFilter.value || undefined,
      keyword: keyword.value.trim() || undefined,
    })
    const url = URL.createObjectURL(blob as unknown as Blob)
    const a = document.createElement('a')
    a.href = url
    const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '')
    a.download = `对账结果_${stamp}.xlsx`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  } finally {
    exporting.value = false
  }
}

async function openDetail(row: ReconciliationReport) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await getReconciliationDetail(row.business_no)
  } finally {
    detailLoading.value = false
  }
}

// ---------------- 差异处理 ----------------
const resolveDialog = ref(false)
const resolving = ref(false)
const resolveForm = ref<{ diff: ReconciliationDiff | null; type: DiffResolutionType | string; note: string }>({
  diff: null,
  type: 'supplement',
  note: '',
})

function openResolve(diff: ReconciliationDiff) {
  resolveForm.value = {
    diff,
    type: (diff.resolution?.type as string) || 'supplement',
    note: diff.resolution?.note || '',
  }
  resolveDialog.value = true
}

async function submitResolve() {
  if (!detail.value || !resolveForm.value.diff) return
  resolving.value = true
  try {
    await resolveReconciliationDiff(detail.value.business_no, {
      key: resolveForm.value.diff.key,
      resolution: resolveForm.value.type,
      note: resolveForm.value.note,
    })
    ElMessage.success(resolveForm.value.type === 'reopen' ? '已撤销处理，重新待办' : '已记录处理结果')
    resolveDialog.value = false
    detail.value = await getReconciliationDetail(detail.value.business_no)
    await refresh()
  } finally {
    resolving.value = false
  }
}

async function urgeBusiness() {
  if (!detail.value) return
  try {
    const { value } = await ElMessageBox.prompt(
      '记录一次催办（可写催办对象、渠道、对方答复）',
      `催办 ${detail.value.business_no}`,
      { inputValue: '', confirmButtonText: '记录催办', cancelButtonText: '取消' },
    )
    const res = await urgeReconciliationBusiness(detail.value.business_no, value || '')
    ElMessage.success(res.message)
    await loadUrges()
  } catch {
    // 用户取消
  }
}

// ---------------- 定时巡检 ----------------
const schedule = ref<ReconcileSchedule | null>(null)
const cronInput = ref('')
const savingSchedule = ref(false)

async function loadSchedule() {
  try {
    schedule.value = await getReconcileSchedule()
    cronInput.value = schedule.value.cron
  } catch {
    schedule.value = null
  }
}

async function toggleSchedule(val: boolean) {
  savingSchedule.value = true
  try {
    schedule.value = await updateReconcileSchedule({ enabled: val })
    ElMessage.success(val ? '已开启每日自动对账' : '已关闭每日自动对账')
  } finally {
    savingSchedule.value = false
  }
}

async function saveCron() {
  savingSchedule.value = true
  try {
    schedule.value = await updateReconcileSchedule({ cron: cronInput.value.trim() })
    ElMessage.success('执行时刻已保存')
  } finally {
    savingSchedule.value = false
  }
}

async function dismissChanges() {
  await clearReconcileChanges()
  await loadSchedule()
  ElMessage.success('变化提醒已清空')
}

// ---------------- 客户 / 期间视角 ----------------
const summaryGroup = ref<'customer' | 'month'>('customer')
const summaryRows = ref<SummaryRow[]>([])
const summaryViewLoading = ref(false)

async function loadSummaryView(group: 'customer' | 'month') {
  summaryGroup.value = group
  summaryViewLoading.value = true
  try {
    const res = await summaryByGroup(group)
    summaryRows.value = res.rows
  } catch {
    summaryRows.value = []
  } finally {
    summaryViewLoading.value = false
  }
}

// ---------------- 催办清单 ----------------
const urges = ref<UrgeItem[]>([])
const urgesLoading = ref(false)

async function loadUrges() {
  urgesLoading.value = true
  try {
    const res = await listUrgeItems()
    urges.value = res.items
  } catch {
    urges.value = []
  } finally {
    urgesLoading.value = false
  }
}

// ---------------- 外部明细 ----------------
const externalRows = ref<ExternalDetail[]>([])
const externalTotal = ref(0)
const externalReceipt = ref(0)
const externalPayment = ref(0)
const externalBusinessNo = ref('')
const externalLoading = ref(false)
const batches = ref<ExternalBatch[]>([])
const importing = ref(false)
const uniqueness = ref<InvoiceUniquenessItem[]>([])

async function loadExternal() {
  externalLoading.value = true
  try {
    const res = await listExternalDetails({
      business_no: externalBusinessNo.value.trim() || undefined,
      limit: 200,
    })
    externalRows.value = res.items
    externalTotal.value = res.total
    externalReceipt.value = res.receipt_total
    externalPayment.value = res.payment_total
  } catch {
    externalRows.value = []
    externalTotal.value = 0
  } finally {
    externalLoading.value = false
  }
}

async function loadBatches() {
  try {
    const res = await listExternalBatches()
    batches.value = res.items
  } catch {
    batches.value = []
  }
}

async function loadUniqueness() {
  try {
    const res = await listInvoiceUniqueness()
    uniqueness.value = res.items
  } catch {
    uniqueness.value = []
  }
}

async function onExternalUpload(options: { file: File }) {
  importing.value = true
  try {
    const res = await importExternalDetails(options.file)
    ElMessage.success(res.message)
    await Promise.all([loadExternal(), loadBatches()])
  } finally {
    importing.value = false
  }
  return false
}

async function removeBatch(batch: ExternalBatch) {
  try {
    await ElMessageBox.confirm(`删除批次 ${batch.batch_no} 的全部 ${batch.rows} 行？`, '删除外部明细批次', {
      type: 'warning',
    })
  } catch {
    return
  }
  await deleteExternalBatch(batch.batch_no)
  ElMessage.success('已删除该批次')
  await Promise.all([loadExternal(), loadBatches()])
}

function onTabChange(name: string) {
  if (name === 'customer' || name === 'month') loadSummaryView(name)
  if (name === 'urge') loadUrges()
  if (name === 'external') {
    loadExternal()
    loadBatches()
    loadUniqueness()
  }
}

onMounted(async () => {
  await refresh()
  await loadSchedule()
})
</script>

<template>
  <div class="recon-page">
    <!-- 变化提醒 -->
    <el-alert
      v-if="schedule && schedule.changed_businesses.length"
      type="warning"
      show-icon
      :closable="false"
      class="change-alert"
    >
      <template #title>
        定时巡检发现 {{ schedule.changed_businesses.length }} 条业务线结果有变化：
        {{ schedule.changed_businesses.slice(0, 6).join('、') }}
        <span v-if="schedule.changed_businesses.length > 6"> 等</span>
      </template>
      <el-button link type="primary" size="small" @click="dismissChanges">标记为已读</el-button>
    </el-alert>

    <!-- 汇总卡 -->
    <el-row :gutter="12" class="summary-row" v-if="activeTab === 'detail'">
      <el-col :xs="12" :sm="12" :md="8">
        <el-card shadow="never" class="summary-card">
          <div class="summary-label">业务线总数</div>
          <div class="summary-value">{{ summary.total }}</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="12" :md="4">
        <el-card shadow="never" class="summary-card ok">
          <div class="summary-label">已对齐</div>
          <div class="summary-value">{{ summary.ok }}</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="12" :md="4">
        <el-card shadow="never" class="summary-card warning">
          <div class="summary-label">有警告</div>
          <div class="summary-value">{{ summary.warning }}</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="12" :md="4">
        <el-card shadow="never" class="summary-card error">
          <div class="summary-label">有错误</div>
          <div class="summary-value">{{ summary.error }}</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="12" :md="4">
        <el-card shadow="never" class="summary-card nodata">
          <div class="summary-label">未上传资料</div>
          <div class="summary-value">{{ summary.no_data }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 工具栏 -->
    <el-card shadow="never" class="toolbar-card" v-if="activeTab === 'detail'">
      <div class="toolbar">
        <div class="toolbar-left">
          <el-select v-model="statusFilter" style="width: 130px" placeholder="全部状态" @change="onFilterChange">
            <el-option v-for="opt in statusOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
          </el-select>
          <el-input
            v-model="keyword"
            placeholder="业务线号 / 上游合同 / 下游合同"
            clearable
            style="width: 260px"
            @keyup.enter="onSearch"
            @clear="onSearch"
          />
          <el-button @click="onSearch">查询</el-button>
        </div>
        <div class="toolbar-right">
          <el-button :loading="exporting" @click="doExport">导出 Excel</el-button>
          <el-button type="primary" :loading="running" @click="runAll">开始对账</el-button>
        </div>
      </div>
      <div class="schedule-row">
        <div class="schedule-left">
          <span class="schedule-label">每日自动对账</span>
          <el-switch
            :model-value="schedule?.enabled || false"
            :loading="savingSchedule"
            @change="toggleSchedule($event as boolean)"
          />
          <el-input v-model="cronInput" size="small" style="width: 150px" placeholder="0 8 * * *" />
          <el-button size="small" :loading="savingSchedule" @click="saveCron">保存时刻</el-button>
          <span class="schedule-hint">格式：分 时 * * *（每天固定时刻）</span>
        </div>
        <div class="schedule-last">
          <span v-if="schedule?.last_run_at">上次：{{ schedule.last_run_at }} · {{ schedule.last_result }}</span>
          <span v-else class="muted">尚未自动执行</span>
          <span v-if="schedule?.last_error" class="schedule-error">{{ schedule.last_error }}</span>
        </div>
      </div>
    </el-card>

    <!-- 多视图 -->
    <el-tabs v-model="activeTab" class="recon-tabs" @tab-change="onTabChange">
      <el-tab-pane label="对账明细" name="detail">
        <el-card shadow="never" class="table-card">
          <el-table
            v-loading="loading"
            :data="items"
            stripe
            size="small"
            @row-click="openDetail"
            class="recon-table"
          >
            <el-table-column prop="business_no" label="业务线号" min-width="170" fixed>
              <template #default="{ row }">
                <span class="biz-no">{{ row.business_no }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="up_contract_no" label="上游合同" min-width="150" show-overflow-tooltip />
            <el-table-column prop="down_contract_no" label="下游合同" min-width="150" show-overflow-tooltip />
            <el-table-column label="上游结算" min-width="130" align="right">
              <template #default="{ row }">{{ fmtAmount(row.up_settlement) }}</template>
            </el-table-column>
            <el-table-column label="下游结算" min-width="130" align="right">
              <template #default="{ row }">{{ fmtAmount(row.down_settlement) }}</template>
            </el-table-column>
            <el-table-column label="应收利息" min-width="110" align="right">
              <template #default="{ row }">{{ fmtAmount(row.interest_receivable) }}</template>
            </el-table-column>
            <el-table-column label="差异" width="80" align="center">
              <template #default="{ row }">
                <span v-if="row.status === 'no_data'" class="muted">—</span>
                <span v-else-if="row.status === 'ok' && row.diff_count > 0" class="muted">
                  {{ row.diff_count }} 提示
                </span>
                <span v-else>{{ row.diff_count }}</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="100" align="center">
              <template #default="{ row }">
                <el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="80" align="center">
              <template #default="{ row }">
                <el-button link type="primary" @click.stop="openDetail(row)">详情</el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty :description="hasReports ? '没有符合条件的业务线' : '还没有对账结果，点击「开始对账」'" />
            </template>
          </el-table>

          <div class="pager">
            <el-pagination
              v-model:current-page="page"
              v-model:page-size="pageSize"
              :total="total"
              :page-sizes="[20, 50, 100]"
              layout="total, sizes, prev, pager, next"
              @current-change="loadList"
              @size-change="onFilterChange"
            />
          </div>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="客户视角" name="customer">
        <el-card shadow="never">
          <el-table v-loading="summaryViewLoading" :data="summaryRows" size="small" stripe>
            <el-table-column prop="label" label="采购商" min-width="200" show-overflow-tooltip />
            <el-table-column prop="business_count" label="业务线" width="80" align="center" />
            <el-table-column label="采购结算" min-width="130" align="right">
              <template #default="{ row }">{{ fmtAmount(row.up_settlement) }}</template>
            </el-table-column>
            <el-table-column label="销售结算" min-width="130" align="right">
              <template #default="{ row }">{{ fmtAmount(row.down_settlement) }}</template>
            </el-table-column>
            <el-table-column label="实收利息" min-width="110" align="right">
              <template #default="{ row }">{{ fmtAmount(row.interest_received) }}</template>
            </el-table-column>
            <el-table-column label="下游回款（外部）" min-width="140" align="right">
              <template #default="{ row }">{{ fmtAmount(row.external_receipt) }}</template>
            </el-table-column>
            <el-table-column label="差异 / 错误 / 警告" width="150" align="center">
              <template #default="{ row }">
                {{ row.diff_count }} / {{ row.error_count }} / {{ row.warning_count }}
              </template>
            </el-table-column>
            <el-table-column label="未上传资料" width="100" align="center" prop="no_data_count" />
            <template #empty><el-empty description="还没有对账结果" :image-size="64" /></template>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="期间视角" name="month">
        <el-card shadow="never">
          <el-table v-loading="summaryViewLoading" :data="summaryRows" size="small" stripe>
            <el-table-column prop="label" label="期间" width="120" />
            <el-table-column prop="business_count" label="业务线" width="80" align="center" />
            <el-table-column label="采购结算" min-width="130" align="right">
              <template #default="{ row }">{{ fmtAmount(row.up_settlement) }}</template>
            </el-table-column>
            <el-table-column label="销售结算" min-width="130" align="right">
              <template #default="{ row }">{{ fmtAmount(row.down_settlement) }}</template>
            </el-table-column>
            <el-table-column label="实收利息" min-width="110" align="right">
              <template #default="{ row }">{{ fmtAmount(row.interest_received) }}</template>
            </el-table-column>
            <el-table-column label="差异 / 错误 / 警告" width="150" align="center">
              <template #default="{ row }">
                {{ row.diff_count }} / {{ row.error_count }} / {{ row.warning_count }}
              </template>
            </el-table-column>
            <el-table-column label="未上传资料" width="100" align="center" prop="no_data_count" />
            <template #empty><el-empty description="还没有对账结果" :image-size="64" /></template>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="催办清单" name="urge">
        <el-card shadow="never">
          <div class="tab-tools">
            <span class="muted">只列还缺资料、且未关闭的业务线；点「详情」可直接催办。</span>
            <el-button size="small" :loading="urgesLoading" @click="loadUrges">刷新</el-button>
          </div>
          <el-table v-loading="urgesLoading" :data="urges" size="small" stripe>
            <el-table-column prop="business_no" label="业务线号" min-width="170">
              <template #default="{ row }"><span class="biz-no">{{ row.business_no }}</span></template>
            </el-table-column>
            <el-table-column prop="customer" label="采购商" min-width="150" show-overflow-tooltip />
            <el-table-column prop="period" label="期间" width="100" />
            <el-table-column label="缺什么" min-width="180">
              <template #default="{ row }">
                {{ (row.missing_roles || []).join('、') || '—' }}
              </template>
            </el-table-column>
            <el-table-column prop="urge_count" label="催办次数" width="90" align="center" />
            <el-table-column prop="last_urged_at" label="最近催办" min-width="140" />
            <el-table-column prop="last_note" label="备注" min-width="150" show-overflow-tooltip />
            <template #empty><el-empty description="没有待催办的业务线" :image-size="64" /></template>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="外部明细" name="external">
        <el-card shadow="never" class="external-card">
          <div class="tab-tools">
            <div class="toolbar-left">
              <el-upload :show-file-list="false" :http-request="onExternalUpload" accept=".xlsx,.xls,.csv">
                <el-button type="primary" :loading="importing">导入明细表</el-button>
              </el-upload>
              <el-input
                v-model="externalBusinessNo"
                placeholder="按业务线号筛选"
                clearable
                style="width: 220px"
                @keyup.enter="loadExternal"
                @clear="loadExternal"
              />
              <el-button @click="loadExternal">查询</el-button>
            </div>
            <span class="muted">
              参考口径，不参与判定：共 {{ externalTotal }} 行，收款 {{ fmtAmount(externalReceipt) }}，付款
              {{ fmtAmount(externalPayment) }}
            </span>
          </div>
          <el-table v-loading="externalLoading" :data="externalRows" size="small" stripe max-height="380">
            <el-table-column prop="business_no" label="业务线号" min-width="170" />
            <el-table-column prop="occurred_on" label="日期" width="110" />
            <el-table-column label="金额" width="130" align="right">
              <template #default="{ row }">{{ fmtAmount(row.amount) }}</template>
            </el-table-column>
            <el-table-column label="方向" width="80" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="row.kind === 'payment' ? 'warning' : 'success'">
                  {{ row.kind === 'payment' ? '付款' : '收款' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="note" label="备注" min-width="150" show-overflow-tooltip />
            <el-table-column prop="source_filename" label="来源" min-width="140" show-overflow-tooltip />
            <template #empty><el-empty description="还没有导入外部明细" :image-size="64" /></template>
          </el-table>
        </el-card>

        <el-card shadow="never" class="external-card">
          <div class="section-title">导入批次</div>
          <el-table :data="batches" size="small" stripe>
            <el-table-column prop="batch_no" label="批次号" min-width="180" />
            <el-table-column prop="source_filename" label="文件" min-width="160" show-overflow-tooltip />
            <el-table-column prop="imported_at" label="导入时间" min-width="140" />
            <el-table-column prop="rows" label="行数" width="80" align="center" />
            <el-table-column prop="receipt" label="收款合计" width="130" align="right" />
            <el-table-column prop="payment" label="付款合计" width="130" align="right" />
            <el-table-column label="操作" width="80" align="center">
              <template #default="{ row }">
                <el-button link type="danger" @click="removeBatch(row)">删除</el-button>
              </template>
            </el-table-column>
            <template #empty><el-empty description="暂无批次" :image-size="64" /></template>
          </el-table>
        </el-card>

        <el-card shadow="never" class="external-card">
          <div class="section-title">
            发票号跨业务线重复（{{ uniqueness.length }}）
            <el-button link type="primary" size="small" @click="loadUniqueness">刷新</el-button>
          </div>
          <el-table :data="uniqueness" size="small" stripe>
            <el-table-column prop="invoice_no" label="发票号" min-width="200" />
            <el-table-column label="出现于" min-width="240">
              <template #default="{ row }">{{ (row.business_nos || []).join('、') }}</template>
            </el-table-column>
            <template #empty><el-empty description="未发现重复发票号" :image-size="64" /></template>
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 详情抽屉 -->
    <el-drawer v-model="detailVisible" :size="drawerSize" :with-header="false" destroy-on-close>
      <div v-loading="detailLoading" class="detail">
        <template v-if="detail">
          <div class="detail-head">
            <div>
              <h3 class="detail-title">{{ detail.business_no }}</h3>
              <div class="detail-sub">
                {{ detail.ledger_sheet }}
                <span v-if="detail.vessel"> · {{ detail.vessel }}</span>
                <span v-if="detail.material"> · {{ detail.material }}</span>
              </div>
            </div>
            <div class="detail-head-right">
              <el-button size="small" @click="urgeBusiness">催办</el-button>
              <el-tag :type="statusType(detail.status)">{{ statusLabel(detail.status) }}</el-tag>
            </div>
          </div>

          <el-alert
            v-if="detail.note"
            :title="detail.note"
            type="info"
            :closable="false"
            show-icon
            class="detail-note"
          />

          <!-- 金额勾稽 -->
          <el-descriptions :column="2" border size="small" class="amount-desc">
            <el-descriptions-item label="上游合同金额">{{ fmtAmount(detail.up_contract_amount) }}</el-descriptions-item>
            <el-descriptions-item label="下游合同金额">{{ fmtAmount(detail.down_contract_amount) }}</el-descriptions-item>
            <el-descriptions-item label="采购结算金额">{{ fmtAmount(detail.up_settlement) }}</el-descriptions-item>
            <el-descriptions-item label="销售结算金额">{{ fmtAmount(detail.down_settlement) }}</el-descriptions-item>
            <el-descriptions-item label="应收利息">{{ fmtAmount(detail.interest_receivable) }}</el-descriptions-item>
            <el-descriptions-item label="实收利息">{{ fmtAmount(detail.interest_received) }}</el-descriptions-item>
            <el-descriptions-item label="供应商">{{ detail.supplier || '—' }}</el-descriptions-item>
            <el-descriptions-item label="采购商">{{ detail.customer || '—' }}</el-descriptions-item>
          </el-descriptions>

          <!-- 2×5 单据矩阵 -->
          <div class="section-title">单据矩阵</div>
          <div v-for="direction in ['up', 'down']" :key="direction" class="matrix-row">
            <div class="matrix-dir">{{ directionLabels[direction] }}</div>
            <div class="matrix-cells">
              <div
                v-for="role in roleOrder"
                :key="role"
                class="matrix-cell"
                :class="{ missing: (cellOf(detail.matrix, direction, role)?.count || 0) === 0 }"
              >
                <div class="cell-role">{{ roleLabels[role] }}</div>
                <div class="cell-main">
                  <template v-if="cellOf(detail.matrix, direction, role)?.count">
                    <span class="cell-ok">✓ {{ cellOf(detail.matrix, direction, role)?.count }} 份</span>
                  </template>
                  <span v-else class="cell-miss">✗ 缺件</span>
                </div>
                <div class="cell-amount">
                  {{ fmtAmount(cellOf(detail.matrix, direction, role)?.amount) }}
                </div>
                <div v-if="cellOf(detail.matrix, direction, role)?.candidate_count" class="cell-hint">
                  库中 {{ cellOf(detail.matrix, direction, role)?.candidate_count }} 份未归属
                </div>
              </div>
            </div>
          </div>

          <!-- 差异明细 -->
          <div class="section-title">差异明细（{{ activeDiffCount }}）</div>
          <el-empty
            v-if="!detail.diffs.length"
            :description="detail.note ? '尚未上传资料，暂无差异' : '没有差异'"
            :image-size="64"
          />
          <el-table v-else :data="detail.diffs" size="small" stripe max-height="340">
            <el-table-column label="级别" width="68" align="center">
              <template #default="{ row }">
                <el-tag :type="severityType(row.severity)" size="small">{{ severityLabel(row.severity) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="类型" width="100">
              <template #default="{ row }">
                {{ diffTypeLabel(row.type) }}
                <el-tag v-if="row.is_new === true" type="danger" size="small" effect="plain" class="new-tag">新增</el-tag>
                <el-tag v-else-if="row.is_new === false" type="info" size="small" effect="plain" class="new-tag">
                  遗留
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="title" label="说明" min-width="180" show-overflow-tooltip />
            <el-table-column label="期望" min-width="100" align="right">
              <template #default="{ row }">{{ row.expected || '—' }}</template>
            </el-table-column>
            <el-table-column label="实际" min-width="100" align="right">
              <template #default="{ row }">{{ row.actual || '—' }}</template>
            </el-table-column>
            <el-table-column label="处理" min-width="110">
              <template #default="{ row }">
                <el-tag :type="resolutionType(row.resolution?.type)" size="small">
                  {{ resolutionLabel(row.resolution?.type) }}
                </el-tag>
                <div v-if="row.resolution?.note" class="resolve-note">{{ row.resolution.note }}</div>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="70" align="center">
              <template #default="{ row }">
                <el-button link type="primary" @click="openResolve(row)">处理</el-button>
              </template>
            </el-table-column>
          </el-table>

          <!-- 已解决 -->
          <template v-if="detail.resolved_diffs && detail.resolved_diffs.length">
            <div class="section-title">已解决（上一轮有、本轮消失，{{ detail.resolved_diffs.length }}）</div>
            <el-table :data="detail.resolved_diffs" size="small" stripe max-height="220">
              <el-table-column label="类型" width="100">
                <template #default="{ row }">{{ diffTypeLabel(row.type) }}</template>
              </el-table-column>
              <el-table-column prop="title" label="说明" min-width="200" show-overflow-tooltip />
              <el-table-column label="期望" min-width="100" align="right">
                <template #default="{ row }">{{ row.expected || '—' }}</template>
              </el-table-column>
              <el-table-column label="实际" min-width="100" align="right">
                <template #default="{ row }">{{ row.actual || '—' }}</template>
              </el-table-column>
              <el-table-column prop="resolved_at" label="消失时间" min-width="140" />
            </el-table>
          </template>
        </template>
        <el-empty v-else-if="!detailLoading" description="暂无数据" />
      </div>
    </el-drawer>

    <!-- 处理差异 -->
    <el-dialog v-model="resolveDialog" title="处理差异" width="460px" append-to-body>
      <div v-if="resolveForm.diff" class="resolve-body">
        <div class="resolve-diff">
          <div class="resolve-diff-title">{{ resolveForm.diff.title }}</div>
          <div class="resolve-diff-meta">
            {{ diffTypeLabel(resolveForm.diff.type) }} · 期望 {{ resolveForm.diff.expected || '—' }} / 实际
            {{ resolveForm.diff.actual || '—' }}
          </div>
        </div>
        <el-radio-group v-model="resolveForm.type" class="resolve-options">
          <el-radio v-for="opt in resolutionOptions" :key="opt.value" :value="opt.value" class="resolve-option">
            <span class="resolve-option-label">{{ opt.label }}</span>
            <span class="resolve-option-desc">{{ opt.desc }}</span>
          </el-radio>
        </el-radio-group>
        <el-input
          v-model="resolveForm.note"
          type="textarea"
          :rows="2"
          placeholder="备注：谁在处理、预计什么时候补齐、对方怎么答复的"
          maxlength="500"
        />
      </div>
      <template #footer>
        <el-button @click="resolveDialog = false">取消</el-button>
        <el-button type="primary" :loading="resolving" @click="submitResolve">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.recon-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.change-alert {
  border-radius: 8px;
}
.summary-row {
  margin-bottom: 0;
}
.summary-card {
  border-radius: 8px;
}
.summary-label {
  color: #6b7280;
  font-size: 13px;
}
.summary-value {
  font-size: 26px;
  font-weight: 700;
  color: #1f2937;
  line-height: 1.2;
  margin-top: 4px;
}
.summary-card.ok .summary-value {
  color: #15803d;
}
.summary-card.warning .summary-value {
  color: #b45309;
}
.summary-card.error .summary-value {
  color: #b91c1c;
}
.summary-card.nodata .summary-value {
  color: #6b7280;
}
.detail-note {
  margin-bottom: 8px;
}
.muted {
  color: #9ca3af;
}
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.toolbar-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.schedule-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed #e5e7eb;
}
.schedule-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.schedule-label {
  font-size: 13px;
  color: #475569;
}
.schedule-hint {
  font-size: 12px;
  color: #9ca3af;
}
.schedule-last {
  font-size: 12px;
  color: #64748b;
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.schedule-error {
  color: #b91c1c;
}
.biz-no {
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-weight: 600;
}
.recon-table :deep(.el-table__row) {
  cursor: pointer;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
.recon-tabs :deep(.el-tabs__header) {
  margin-bottom: 12px;
}
.tab-tools {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.external-card {
  margin-bottom: 12px;
}
.detail {
  padding: 4px;
  min-height: 200px;
}
.detail-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 12px;
  gap: 12px;
}
.detail-head-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.detail-title {
  margin: 0;
  font-size: 18px;
  font-family: ui-monospace, Menlo, Consolas, monospace;
}
.detail-sub {
  color: #6b7280;
  font-size: 13px;
  margin-top: 4px;
}
.amount-desc {
  margin-bottom: 8px;
}
.section-title {
  font-weight: 600;
  color: #303133;
  margin: 16px 0 8px;
}
.matrix-row {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}
.matrix-dir {
  flex: 0 0 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 600;
  color: #475569;
  background: #f1f5f9;
  border-radius: 6px;
}
.matrix-cells {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
  flex: 1;
}
.matrix-cell {
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 8px;
  background: #f8fafc;
  min-width: 0;
}
.matrix-cell.missing {
  border-color: #fecaca;
  background: #fef2f2;
}
.cell-role {
  font-size: 12px;
  color: #64748b;
  margin-bottom: 4px;
}
.cell-main {
  font-size: 14px;
  font-weight: 600;
}
.cell-ok {
  color: #15803d;
}
.cell-miss {
  color: #b91c1c;
}
.cell-amount {
  font-size: 12px;
  color: #475569;
  margin-top: 4px;
}
.cell-hint {
  font-size: 11px;
  color: #b45309;
  margin-top: 4px;
}
.new-tag {
  margin-left: 4px;
}
.resolve-note {
  font-size: 11px;
  color: #6b7280;
  margin-top: 2px;
}
.resolve-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.resolve-diff {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 8px 10px;
}
.resolve-diff-title {
  font-weight: 600;
  font-size: 13px;
  color: #1f2937;
}
.resolve-diff-meta {
  font-size: 12px;
  color: #64748b;
  margin-top: 4px;
}
.resolve-options {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
}
.resolve-option {
  height: auto;
  align-items: flex-start;
}
.resolve-option-label {
  font-weight: 600;
  margin-right: 6px;
}
.resolve-option-desc {
  font-size: 12px;
  color: #9ca3af;
}
@media (max-width: 768px) {
  .summary-value {
    font-size: 20px;
  }
  .matrix-cells {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
