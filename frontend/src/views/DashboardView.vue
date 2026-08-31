<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { getDashboardStats, type DashboardStats } from '@/api'

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
  { key: 'total_documents', label: '文档总数', color: '#409eff', icon: '📄' },
  { key: 'total_archived', label: '已归档', color: '#67c23a', icon: '📁' },
  { key: 'pending_review', label: '待人工确认', color: '#e6a23c', icon: '📌' },
  { key: 'today_total', label: '今日新增', color: '#909399', icon: '🗓️' },
] as const
</script>

<template>
  <div v-loading="loading">
    <el-row :gutter="16">
      <el-col v-for="c in cards" :key="c.key" :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-icon" :style="{ background: c.color + '1a', color: c.color }">
            {{ c.icon }}
          </div>
          <div class="stat-value" :style="{ color: c.color }">
            {{ stats ? (stats as any)[c.key] : '—' }}
          </div>
          <div class="stat-label">{{ c.label }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="section">
      <template #header>
        <span>系统状态</span>
      </template>
      <el-descriptions :column="2" border>
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
}
.section {
  margin-top: 16px;
}
</style>
