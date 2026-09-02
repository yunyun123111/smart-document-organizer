<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'
import {
  documentFileUrl,
  getDashboardStats,
  getDashboardTrends,
  listDocuments,
  type DashboardStats,
  type DashboardTrends,
  type DocumentListItem,
} from '@/api'

const router = useRouter()
const stats = ref<DashboardStats | null>(null)
const trends = ref<DashboardTrends | null>(null)
const loading = ref(false)

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

function openFile(id: number) {
  window.open(documentFileUrl(id), '_blank')
}

function fmtSize(n: number): string {
  if (n > 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB'
  if (n > 1024) return (n / 1024).toFixed(1) + ' KB'
  return n + ' B'
}

const trendChartRef = ref<HTMLDivElement>()
const rateChartRef = ref<HTMLDivElement>()
let trendChart: echarts.ECharts | null = null
let rateChart: echarts.ECharts | null = null

async function load() {
  loading.value = true
  try {
    stats.value = await getDashboardStats()
    trends.value = await getDashboardTrends()
    await nextTick()
    renderCharts()
  } finally {
    loading.value = false
  }
}

function renderCharts() {
  if (!trends.value) return
  renderTrendChart()
  renderRateChart()
}

function renderTrendChart() {
  if (!trendChartRef.value) return
  trendChart?.dispose()
  trendChart = echarts.init(trendChartRef.value)
  const d = trends.value!.daily
  const dates = d.map((x) => x.date.slice(5))
  trendChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { orient: 'vertical', right: 8, top: 'middle' },
    grid: { left: 40, right: 96, top: 30, bottom: 28 },
    xAxis: { type: 'category', data: dates },
    yAxis: { type: 'value', minInterval: 1 },
    series: [
      // 堆叠柱：柱高 = 当日新增总量；新增折线叠加显示总量趋势
      { name: '新增', type: 'line', data: d.map((x) => x.new), itemStyle: { color: '#409eff' }, smooth: true, symbol: 'circle', symbolSize: 6 },
      { name: '归档', type: 'bar', stack: 'total', data: d.map((x) => x.archived), itemStyle: { color: '#67c23a' }, barWidth: 20 },
      { name: '待确认', type: 'bar', stack: 'total', data: d.map((x) => x.need_review), itemStyle: { color: '#e6a23c' } },
      { name: '失败', type: 'bar', stack: 'total', data: d.map((x) => x.failed), itemStyle: { color: '#f56c6c' } },
    ],
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
    grid: { left: 40, right: 96, top: 30, bottom: 28 },
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

function onResize() {
  trendChart?.resize()
  rateChart?.resize()
}

onMounted(() => {
  load()
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  trendChart?.dispose()
  rateChart?.dispose()
})

const cards = [
  { key: 'total_documents', label: '文档总数', color: '#409eff', icon: '📄', to: '/library' },
  { key: 'total_archived', label: '已归档', color: '#67c23a', icon: '📁', to: '/library?status=archived' },
  { key: 'pending_review', label: '待人工确认', color: '#e6a23c', icon: '📌', to: '/review' },
  { key: 'today_total', label: '今日新增', color: '#909399', icon: '🗓️', to: '/library' },
] as const

function go(to: string) {
  router.push(to)
}

const typeColors = ['#409eff', '#67c23a', '#e6a23c', '#f56c6c', '#909399', '#9c27b0', '#00bcd4', '#ff9800', '#795548']
function typeColor(i: number) {
  return typeColors[i % typeColors.length]
}

function alertType(level: string): 'error' | 'warning' | 'info' | 'success' {
  if (level === 'error') return 'error'
  if (level === 'warning') return 'warning'
  return 'info'
}
</script>

<template>
  <div v-loading="loading">
    <!-- 异常提醒 -->
    <el-card v-if="trends && trends.alerts.length" shadow="never" class="section alert-card">
      <template #header><span>⚠️ 异常提醒</span></template>
      <el-alert
        v-for="(a, i) in trends.alerts"
        :key="i"
        :type="alertType(a.level)"
        :closable="false"
        show-icon
        class="alert-item"
      >
        <template #title>
          <span>{{ a.message }}</span>
          <el-link v-if="a.link" :type="alertType(a.level)" class="alert-link" @click="go(a.link)">
            查看 →
          </el-link>
        </template>
      </el-alert>
    </el-card>

    <el-row :gutter="16">
      <el-col v-for="c in cards" :key="c.key" :xs="12" :sm="12" :md="6">
        <el-card shadow="hover" class="stat-card" @click="go(c.to)">
          <div class="stat-icon" :style="{ background: c.color + '1a', color: c.color }">
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
            <div class="card-head">
              <span>每日新增趋势（近 {{ trends?.days ?? 14 }} 天）</span>
            </div>
          </template>
          <div ref="trendChartRef" class="chart"></div>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="12">
        <el-card shadow="never">
          <template #header><span>识别成功率 / OCR 失败率趋势</span></template>
          <div ref="rateChartRef" class="chart"></div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="section">
      <el-col :xs="24" :md="12">
        <el-card shadow="never">
          <template #header>
            <div class="card-head">
              <span>文件类型分布（点击查看）</span>
              <el-link type="primary" @click="go('/library')">全部文件 →</el-link>
            </div>
          </template>
          <div v-if="stats && stats.type_counts.length" class="type-list">
            <div
              v-for="(t, i) in stats.type_counts"
              :key="t.type"
              class="type-item"
              @click="openTypeDocs(t.type)"
            >
              <span class="type-dot" :style="{ background: typeColor(i) }"></span>
              <span class="type-name">{{ t.type }}</span>
              <span class="type-count" :style="{ color: typeColor(i) }">{{ t.count }} 份</span>
              <el-icon class="type-arrow"><ArrowRight /></el-icon>
            </div>
          </div>
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

    <el-card shadow="never" class="section">
      <template #header>
        <span>使用提示</span>
      </template>
      <el-steps direction="vertical" :active="4">
        <el-step title="放入文件" description="把杂乱文件拖入「待整理目录」，或在「文件整理」页上传" />
        <el-step title="智能整理" description="系统自动识别类型、提取字段、计算置信度" />
        <el-step title="人工确认" description="置信度不足的文件进入「待人工确认」列表" />
        <el-step title="归档完成" description="确认后按「分类/年/月」自动归档，支持随时撤销" />
      </el-steps>
    </el-card>
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
  padding: 8px 0;
  cursor: pointer;
  transition: transform 0.15s;
}
.stat-card:hover {
  transform: translateY(-3px);
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
.chart {
  width: 100%;
  height: 280px;
}
.type-list {
  display: flex;
  flex-direction: column;
}
.type-item {
  display: flex;
  align-items: center;
  padding: 10px 8px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s;
}
.type-item:hover {
  background: #f5f7fa;
}
.type-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  margin-right: 12px;
  flex-shrink: 0;
}
.type-name {
  flex: 1;
  font-size: 14px;
  color: #303133;
}
.type-count {
  font-weight: 600;
  font-size: 15px;
  margin-right: 8px;
}
.type-arrow {
  color: #c0c4cc;
  font-size: 12px;
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
