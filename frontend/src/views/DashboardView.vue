<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'
import {
  getDashboardStats,
  getDashboardTrends,
  listDocuments,
  openDocumentFile,
  type DashboardAlert,
  type DashboardStats,
  type DashboardTrends,
  type DocumentListItem,
} from '@/api'

const router = useRouter()
const stats = ref<DashboardStats | null>(null)
const trends = ref<DashboardTrends | null>(null)
const loading = ref(false)

// 趋势图：时间范围（7/14/30 天）+ 对比模式
const trendDays = ref(14)
const compareMode = ref(false)

// 类型抽屉：点击类型分布直接看该类型文件
const typeDrawer = ref(false)
const typeDrawerTitle = ref('')
const typeDocs = ref<DocumentListItem[]>([])
const typeDocsLoading = ref(false)

async function openTypeDocs(t: string) {
  typeDrawerTitle.value = `${t}（${stats.value?.type_counts.find((x) => x.type === t)?.count ?? 0} 份）`
  typeDrawer.value = true
  typeDocsLoading.value = true
  typeDocs.value = []
  try {
    typeDocs.value = await listDocuments({ document_type: t, limit: 200 })
  } finally {
    typeDocsLoading.value = false
  }
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
  } catch {
    // 打开失败由 http 拦截器提示
  }
}

function fmtSize(n: number): string {
  if (n > 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB'
  if (n > 1024) return (n / 1024).toFixed(1) + ' KB'
  return n + ' B'
}

// ---------- 图表 ----------
const trendChartRef = ref<HTMLDivElement>()
const rateChartRef = ref<HTMLDivElement>()
const typePieRef = ref<HTMLDivElement>()
let trendChart: echarts.ECharts | null = null
let rateChart: echarts.ECharts | null = null
let typePieChart: echarts.ECharts | null = null

async function load() {
  loading.value = true
  try {
    stats.value = await getDashboardStats()
    trends.value = await getDashboardTrends(trendDays.value)
    await nextTick()
    renderCharts()
  } finally {
    loading.value = false
  }
}

function onDaysChange() {
  load()
}

function renderCharts() {
  if (!trends.value) return
  renderTrendChart()
  renderRateChart()
  renderTypePie()
}

function renderTrendChart() {
  if (!trendChartRef.value) return
  trendChart?.dispose()
  trendChart = echarts.init(trendChartRef.value)
  const d = trends.value!.daily
  const dates = d.map((x) => x.date.slice(5))
  const ocrFail =
    trends.value!.ocr_daily.map((x) =>
      x.fail_rate == null ? null : +(x.fail_rate * 100).toFixed(1),
    )
  const series: any[] = [
    { name: '新增', type: 'line', data: d.map((x) => x.new), itemStyle: { color: '#409eff' }, smooth: true, symbol: 'circle', symbolSize: 6, z: 3 },
    { name: '归档', type: 'bar', stack: 'total', data: d.map((x) => x.archived), itemStyle: { color: '#67c23a' }, barWidth: 18 },
    { name: '待确认', type: 'bar', stack: 'total', data: d.map((x) => x.need_review), itemStyle: { color: '#e6a23c' } },
    { name: '失败', type: 'bar', stack: 'total', data: d.map((x) => x.failed), itemStyle: { color: '#f56c6c' } },
  ]
  const yAxes: any[] = [{ type: 'value', minInterval: 1 }]
  if (compareMode.value) {
    series.push({
      name: 'OCR 失败率', type: 'line', yAxisIndex: 1, smooth: true, connectNulls: true,
      data: ocrFail, itemStyle: { color: '#f56c6c', type: 'dashed' }, symbol: 'diamond', symbolSize: 5,
    })
    yAxes.push({
      type: 'value', max: 100, splitLine: { show: false },
      axisLabel: { formatter: '{value}%' },
    })
  }
  trendChart.setOption({
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v: any) => (v == null ? '—' : `${v}${typeof v === 'number' && v > 100 ? '' : ' 份'}`),
    },
    legend: { orient: 'vertical', right: 8, top: 'middle' },
    grid: { left: 44, right: compareMode.value ? 60 : 96, top: 30, bottom: 28 },
    xAxis: { type: 'category', data: dates },
    yAxis: yAxes,
    series,
  })
}

function renderRateChart() {
  if (!rateChartRef.value) return
  rateChart?.dispose()
  rateChart = echarts.init(rateChartRef.value)
  const d = trends.value!.daily
  const o = trends.value!.ocr_daily
  const dates = d.map((x) => x.date.slice(5))
  const success = d.map((x) => (x.success_rate == null ? null : +(x.success_rate * 100).toFixed(1)))
  const ocrFail = o.map((x) => (x.fail_rate == null ? null : +(x.fail_rate * 100).toFixed(1)))
  rateChart.setOption({
    tooltip: { trigger: 'axis', valueFormatter: (v: any) => (v == null ? '—' : v + '%') },
    legend: { orient: 'vertical', right: 8, top: 'middle' },
    grid: { left: 44, right: 96, top: 30, bottom: 28 },
    xAxis: { type: 'category', data: dates },
    yAxis: { type: 'value', axisLabel: { formatter: '{value}%' }, max: 100 },
    series: [
      {
        name: '识别成功率',
        type: 'line',
        smooth: true,
        connectNulls: true,
        data: success,
        itemStyle: { color: '#67c23a' },
        areaStyle: { opacity: 0.1 },
      },
      {
        name: 'OCR 失败率',
        type: 'line',
        smooth: true,
        connectNulls: true,
        data: ocrFail,
        itemStyle: { color: '#f56c6c' },
      },
    ],
  })
}

// ---------- 文件类型分布：统一颜色规范 + 饼图 ----------
const TYPE_COLOR_MAP: Record<string, string> = {
  '发票': '#f56c6c',
  '销售合同': '#409eff',
  '采购合同': '#409eff',
  '合同': '#409eff',
  '结算单': '#e6a23c',
  '出货单': '#67c23a',
  '入库单': '#9c27b0',
  '报价单': '#00bcd4',
  '订单': '#ff9800',
  '收付款凭证': '#795548',
  '银行回单': '#607d8b',
  '对账单': '#8bc34a',
}
const TYPE_PALETTE = ['#409eff', '#67c23a', '#e6a23c', '#f56c6c', '#9c27b0', '#00bcd4', '#ff9800', '#795548', '#607d8b', '#8bc34a']
function typeColor(t: string, i: number) {
  return TYPE_COLOR_MAP[t] ?? TYPE_PALETTE[i % TYPE_PALETTE.length]
}

function renderTypePie() {
  if (!typePieRef.value) return
  typePieChart?.dispose()
  typePieChart = echarts.init(typePieRef.value)
  const counts = stats.value?.type_counts ?? []
  if (counts.length === 0) return
  const data = counts.map((t, i) => ({
    name: t.type, value: t.count, itemStyle: { color: typeColor(t.type, i) },
  }))
  typePieChart.setOption({
    tooltip: { trigger: 'item', formatter: '{b}<br/>{c} 份（{d}%）' },
    legend: { orient: 'vertical', right: 8, top: 'middle', type: 'scroll', itemWidth: 10, itemHeight: 10 },
    series: [
      {
        name: '文件类型分布',
        type: 'pie',
        radius: ['42%', '68%'],
        center: ['40%', '50%'],
        avoidLabelOverlap: true,
        itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
        label: { show: true, formatter: '{b}\n{c}份', fontSize: 11 },
        emphasis: { label: { show: true, fontWeight: 700, fontSize: 13 } },
        data,
      },
    ],
  })
  typePieChart.on('click', (p: any) => { void openTypeDocs(p.name) })
}

function onResize() {
  trendChart?.resize()
  rateChart?.resize()
  typePieChart?.resize()
}

onMounted(() => {
  loadDismissed()
  load()
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  trendChart?.dispose()
  rateChart?.dispose()
  typePieChart?.dispose()
})

// ---------- 统计卡片 ----------
const cards = [
  { key: 'total_documents', label: '文档总数', color: '#409eff', icon: '📄', to: '/library' },
  { key: 'total_archived', label: '已归档', color: '#67c23a', icon: '📁', to: '/library?status=archived' },
  { key: 'pending_review', label: '待人工确认', color: '#e6a23c', icon: '📌', to: '/review' },
  { key: 'today_total', label: '今日新增', color: '#909399', icon: '🗓️', to: '/library' },
] as const

function go(to: string) {
  router.push(to)
}

function alertType(level: string): 'error' | 'warning' | 'info' | 'success' {
  if (level === 'error') return 'error'
  if (level === 'warning') return 'warning'
  return 'info'
}

// ---- 异常提醒手动关闭（本地持久化，刷新不复活）----
const DISMISS_KEY = 'sdo_dismissed_alerts'
const dismissed = ref<string[]>([])
function alertKey(a: DashboardAlert) {
  return `${a.kind}|${a.type ?? ''}|${a.message}`
}
function loadDismissed() {
  try {
    dismissed.value = JSON.parse(localStorage.getItem(DISMISS_KEY) || '[]')
  } catch {
    dismissed.value = []
  }
}
function visibleAlerts(): DashboardAlert[] {
  if (!trends.value) return []
  return trends.value.alerts.filter((a) => !dismissed.value.includes(alertKey(a)))
}
function dismissAlert(a: DashboardAlert) {
  if (!dismissed.value.includes(alertKey(a))) dismissed.value.push(alertKey(a))
  localStorage.setItem(DISMISS_KEY, JSON.stringify(dismissed.value))
}
function restoreAlerts() {
  dismissed.value = []
  localStorage.setItem(DISMISS_KEY, '[]')
}
</script>

<template>
  <div v-loading="loading">
    <!-- 异常提醒 -->
    <el-card v-if="visibleAlerts().length || dismissed.length" shadow="never" class="section alert-card">
      <template #header>
        <div class="card-head">
          <span>⚠️ 异常提醒</span>
          <el-link v-if="dismissed.length" type="info" :underline="false" class="restore-link" @click="restoreAlerts">
            恢复已关闭提醒（{{ dismissed.length }}）
          </el-link>
        </div>
      </template>
      <el-alert
        v-for="a in visibleAlerts()"
        :key="alertKey(a)"
        :type="alertType(a.level)"
        show-icon
        closable
        class="alert-item"
        @close="dismissAlert(a)"
      >
        <template #title>
          <span>{{ a.message }}</span>
          <el-link v-if="a.link" :type="alertType(a.level)" class="alert-link" @click="go(a.link)">
            查看 →
          </el-link>
        </template>
      </el-alert>
      <div v-if="visibleAlerts().length === 0" class="no-alert-tip">暂无异常提醒</div>
    </el-card>

    <!-- 统计卡片 -->
    <el-row :gutter="16">
      <el-col v-for="c in cards" :key="c.key" :xs="12" :sm="12" :md="6">
        <el-card
          shadow="hover"
          class="stat-card"
          :style="{
            background: `linear-gradient(135deg, ${c.color}12 0%, #ffffff 62%)`,
            borderLeft: `4px solid ${c.color}`,
          }"
          @click="go(c.to)"
        >
          <div class="stat-icon" :style="{ background: c.color + '1f', color: c.color }">
            {{ c.icon }}
          </div>
          <div class="stat-value" :style="{ color: c.color }">
            {{ stats ? (stats as any)[c.key] : '—' }}
          </div>
          <div class="stat-label">
            {{ c.label }}
            <el-icon class="go-icon"><ArrowRight /></el-icon>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 趋势图 -->
    <el-row :gutter="16" class="section">
      <el-col :xs="24" :md="12">
        <el-card shadow="never">
          <template #header>
            <div class="card-head wrap">
              <span>每日新增趋势（近 {{ trendDays }} 天）</span>
              <div class="chart-ops">
                <el-switch v-model="compareMode" size="small" active-text="对比OCR失败率" @change="renderTrendChart" />
                <el-radio-group v-model="trendDays" size="small" @change="onDaysChange">
                  <el-radio-button label="7">7天</el-radio-button>
                  <el-radio-button label="14">14天</el-radio-button>
                  <el-radio-button label="30">30天</el-radio-button>
                </el-radio-group>
              </div>
            </div>
          </template>
          <div ref="trendChartRef" class="chart"></div>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="12">
        <el-card shadow="never">
          <template #header>
            <div class="card-head wrap">
              <span>识别成功率 / OCR 失败率趋势</span>
              <el-radio-group v-model="trendDays" size="small" @change="onDaysChange">
                <el-radio-button label="7">7天</el-radio-button>
                <el-radio-button label="14">14天</el-radio-button>
                <el-radio-button label="30">30天</el-radio-button>
              </el-radio-group>
            </div>
          </template>
          <div ref="rateChartRef" class="chart"></div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="section">
      <el-col :xs="24" :md="12">
        <el-card shadow="never">
          <template #header>
            <div class="card-head">
              <span>文件类型分布（点击查看该类型文件）</span>
              <el-link type="primary" @click="go('/library')">全部文件 →</el-link>
            </div>
          </template>
          <div v-if="stats && stats.type_counts.length" ref="typePieRef" class="chart pie-chart"></div>
          <el-empty v-else description="暂无文件" :image-size="80" />
        </el-card>
      </el-col>

      <el-col :xs="24" :md="12">
        <el-card shadow="never">
          <template #header><span>系统状态</span></template>
          <el-descriptions :column="1" border>
            <el-descriptions-item label="文档根目录">
              {{ stats?.document_root ?? '—' }}
            </el-descriptions-item>
            <el-descriptions-item label="待整理目录（收件箱）">
              {{ stats?.inbox_root ?? '—' }}
            </el-descriptions-item>
            <el-descriptions-item label="OCR 识别">
              <el-tag :type="stats?.ocr_enabled ? 'success' : 'info'">
                {{ stats?.ocr_enabled ? '已启用' : '未启用' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="AI 辅助">
              <el-tag :type="stats?.ai_enabled ? 'success' : 'info'">
                {{ stats?.ai_enabled ? '已启用' : '未配置（纯规则模式）' }}
              </el-tag>
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
    </el-row>
  </div>

  <!-- 类型文件抽屉：点击类型分布直接看该类型文件 -->
  <el-drawer v-model="typeDrawer" :title="typeDrawerTitle" size="72%">
    <div v-loading="typeDocsLoading">
      <el-empty v-if="!typeDocsLoading && typeDocs.length === 0" description="该类型暂无文件" :image-size="80" />
      <div v-for="d in typeDocs" :key="d.id" class="type-doc-item">
        <div class="type-doc-info">
          <div class="type-doc-name">{{ d.current_filename || d.original_filename }}</div>
          <div class="type-doc-meta">
            <el-tag size="small" :type="statusTag(d.status)">{{ statusLabel(d.status) }}</el-tag>
            <span>{{ fmtSize(d.file_size) }}</span>
            <span v-if="d.confidence != null" class="gray">置信度 {{ Math.round(d.confidence * 100) }}%</span>
          </div>
        </div>
        <div class="type-doc-actions">
          <el-button size="small" @click="openFile(d.id)">打开</el-button>
          <el-button size="small" text type="primary" @click="go('/library?type=' + encodeURIComponent(typeDrawerTitle.split('（')[0]))">
            在文档库中查看
          </el-button>
        </div>
      </div>
    </div>
  </el-drawer>
</template>

<style scoped>
.stat-card {
  text-align: center;
  padding: 14px 0;
  cursor: pointer;
  transition: transform 0.15s, box-shadow 0.15s;
  border-radius: 10px;
}
.stat-card:hover {
  transform: translateY(-3px);
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.1) !important;
}
.stat-icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  margin: 0 auto 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
}
.stat-value {
  font-size: 28px;
  font-weight: 700;
}
.stat-label {
  color: #909399;
  margin-top: 6px;
  font-size: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
}
.go-icon {
  font-size: 12px;
}
.section {
  margin-top: 16px;
}
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.card-head.wrap {
  flex-wrap: wrap;
  gap: 8px;
}
.chart-ops {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.alert-card {
  border-color: #f3d19e;
}
.alert-item {
  margin-bottom: 8px;
}
.alert-item:last-child {
  margin-bottom: 0;
}
.alert-link {
  margin-left: 12px;
}
.restore-link {
  font-size: 12px;
}
.no-alert-tip {
  color: #909399;
  font-size: 13px;
  padding: 4px 0;
}
.chart {
  width: 100%;
  height: 300px;
}
.pie-chart {
  height: 300px;
}
.type-doc-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 8px;
  border-bottom: 1px solid #f0f2f5;
}
.type-doc-item:last-child {
  border-bottom: none;
}
.type-doc-info {
  min-width: 0;
  flex: 1;
}
.type-doc-name {
  font-size: 13px;
  color: #303133;
  word-break: break-all;
}
.type-doc-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  color: #909399;
  font-size: 12px;
}
.type-doc-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}
.gray {
  color: #909399;
}
</style>
