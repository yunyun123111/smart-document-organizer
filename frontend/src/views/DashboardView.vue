<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { getDashboardStats, type DashboardStats } from '@/api'

const router = useRouter()
const stats = ref<DashboardStats | null>(null)
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    stats.value = await getDashboardStats()
  } finally {
    loading.value = false
  }
}

onMounted(load)

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
</script>

<template>
  <div v-loading="loading">
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
              @click="go('/library?type=' + encodeURIComponent(t.type))"
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
</style>
