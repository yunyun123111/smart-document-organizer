<script setup lang="ts">
/**
 * 业务线台账自动对账
 * - 顶部：汇总统计 + 一键对账
 * - 中部：业务线列表（状态/关键词筛选、分页），一行一条业务线
 * - 右侧抽屉：金额勾稽 + 2×5 单据矩阵 + 差异明细（可忽略）
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useMobile } from '@/composables/useMobile'
import {
  getReconciliationDetail,
  listReconciliation,
  resolveReconciliationDiff,
  runReconciliation,
  type ReconciliationDetail,
  type ReconciliationDiff,
  type ReconciliationMatrixCell,
  type ReconciliationReport,
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

// ---------------- 列表 ----------------
const items = ref<ReconciliationReport[]>([])
const total = ref(0)
const loading = ref(false)
const running = ref(false)
const statusFilter = ref('')
const keyword = ref('')
const page = ref(1)
const pageSize = ref(20)

// ---------------- 详情 ----------------
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<ReconciliationDetail | null>(null)
const { isMobile } = useMobile()
const drawerSize = computed(() => (isMobile.value ? '94%' : '720px'))

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

const hasReports = computed(() => summary.value.total > 0)

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
  } finally {
    running.value = false
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

async function ignoreDiff(diff: ReconciliationDiff) {
  if (!detail.value) return
  const sameType = detail.value.diffs.filter((d) => d.type === diff.type)
  const index = sameType.indexOf(diff)
  await resolveReconciliationDiff(detail.value.business_no, { diff_type: diff.type, index })
  ElMessage.success('已忽略该差异')
  detail.value = await getReconciliationDetail(detail.value.business_no)
  await refresh()
}

onMounted(refresh)
</script>

<template>
  <div class="recon-page">
    <!-- 汇总卡 -->
    <el-row :gutter="12" class="summary-row">
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
    <el-card shadow="never" class="toolbar-card">
      <div class="toolbar">
        <div class="toolbar-left">
          <el-select
            v-model="statusFilter"
            style="width: 130px"
            placeholder="全部状态"
            @change="onFilterChange"
          >
            <el-option
              v-for="opt in statusOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
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
        <el-button type="primary" :loading="running" @click="runAll">开始对账</el-button>
      </div>
    </el-card>

    <!-- 列表 -->
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
            <!-- 未上传资料时矩阵本来就全空，差异条数没有参考意义，显示“—” -->
            <span v-if="row.status === 'no_data'" class="muted">—</span>
            <!-- 已对齐但有提示：说明金额已勾稽通过，剩下的都是无需处理的说明 -->
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
            <el-tag :type="statusType(detail.status)">{{ statusLabel(detail.status) }}</el-tag>
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
                <div
                  v-if="cellOf(detail.matrix, direction, role)?.candidate_count"
                  class="cell-hint"
                >
                  库中 {{ cellOf(detail.matrix, direction, role)?.candidate_count }} 份未归属
                </div>
              </div>
            </div>
          </div>

          <!-- 差异明细 -->
          <div class="section-title">差异明细（{{ detail.diffs.filter((d) => !d.ignored).length }}）</div>
          <el-empty
            v-if="!detail.diffs.length"
            :description="detail.note ? '尚未上传资料，暂无差异' : '没有差异'"
            :image-size="64"
          />
          <el-table v-else :data="detail.diffs" size="small" stripe max-height="320">
            <el-table-column label="级别" width="72" align="center">
              <template #default="{ row }">
                <el-tag :type="severityType(row.severity)" size="small">{{ severityLabel(row.severity) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="类型" width="96">
              <template #default="{ row }">{{ diffTypeLabel(row.type) }}</template>
            </el-table-column>
            <el-table-column prop="title" label="说明" min-width="180" show-overflow-tooltip />
            <el-table-column label="期望" min-width="110" align="right">
              <template #default="{ row }">{{ row.expected || '—' }}</template>
            </el-table-column>
            <el-table-column label="实际" min-width="110" align="right">
              <template #default="{ row }">{{ row.actual || '—' }}</template>
            </el-table-column>
            <el-table-column label="操作" width="72" align="center">
              <template #default="{ row }">
                <el-button v-if="!row.ignored" link type="primary" @click="ignoreDiff(row)">忽略</el-button>
                <span v-else class="ignored-text">已忽略</span>
              </template>
            </el-table-column>
          </el-table>
        </template>
        <el-empty v-else-if="!detailLoading" description="暂无数据" />
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.recon-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
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
.detail {
  padding: 4px;
  min-height: 200px;
}
.detail-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 12px;
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
.ignored-text {
  color: #9ca3af;
  font-size: 12px;
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
