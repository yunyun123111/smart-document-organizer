<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { UploadFile, UploadFiles } from 'element-plus'
import {
  cancelJob,
  getJob,
  listDocuments,
  startProcessing,
  uploadDocument,
  type ProcessingJob,
} from '@/api'

const uploading = ref(false)
const running = ref<ProcessingJob | null>(null)
const inboxFiles = ref<any[]>([])
const pollTimer = ref<number | null>(null)

// 扫描收件箱已有文件（pending 状态）
async function loadInbox() {
  inboxFiles.value = await listDocuments({ status: 'pending', limit: 100 })
}

async function handleUpload(f: File) {
  uploading.value = true
  try {
    const res = await uploadDocument(f)
    if (res.status === 'duplicate') {
      ElMessage.warning(`「${res.filename}」已在库中（重复）`)
    } else {
      ElMessage.success(`「${res.filename}」已上传待整理`)
    }
    await loadInbox()
  } finally {
    uploading.value = false
  }
}

function onFilesChange(list: File[]) {
  for (const f of list) handleUpload(f)
}

async function start() {
  if (running.value) return
  try {
    const job = await startProcessing()
    running.value = job
    ElMessage.success('批量整理已开始')
    // 若任务瞬间结束（如收件箱为空），直接收尾，避免按钮一直转圈
    if (isFinished(job)) {
      finish(job)
      return
    }
    poll()
  } catch {
    /* 错误已在拦截器提示 */
  }
}

function isFinished(job: ProcessingJob): boolean {
  return job.status === 'completed' || job.status === 'cancelled' || job.status === 'failed'
}

async function finish(job: ProcessingJob) {
  stopPoll()
  running.value = null
  ElMessage.success(
    `整理完成：成功 ${job.success_count} / 待审核 ${job.review_count} / 重复 ${job.duplicate_count} / 失败 ${job.failed_count}`,
  )
  await loadInbox()
}

async function stop() {
  if (running.value) {
    await cancelJob(running.value.id)
    ElMessage.info('已请求取消')
  }
}

function poll() {
  stopPoll()
  pollTimer.value = window.setInterval(async () => {
    if (!running.value) return
    try {
      const job = await getJob(running.value.id)
      if (!running.value) return
      running.value = job
      if (isFinished(job)) {
        finish(job)
      }
    } catch {
      /* 轮询失败保留当前状态，下一轮继续，避免界面卡死 */
    }
  }, 1000)
}

function stopPoll() {
  if (pollTimer.value !== null) {
    clearInterval(pollTimer.value)
    pollTimer.value = null
  }
}

function percent(): number {
  if (!running.value || running.value.total_files === 0) return 0
  return Math.round((running.value.processed_files / running.value.total_files) * 100)
}

onMounted(loadInbox)
onUnmounted(stopPoll)
</script>

<template>
  <el-card shadow="never">
    <template #header>
      <span>批量整理</span>
    </template>

    <el-alert
      title="把需要整理的文件放入「待整理目录」或直接上传，系统会自动识别、分类并归档；置信度不足的会进入人工确认。"
      type="info"
      :closable="false"
      class="mb16"
    />

    <div class="upload-area">
      <el-upload
        drag
        multiple
        :auto-upload="false"
        :show-file-list="false"
        :on-change="(_f: UploadFile, fl: UploadFiles) => onFilesChange(fl.map((i) => i.raw).filter(Boolean) as File[])"
      >
        <el-icon class="el-icon--upload"><upload-filled /></el-icon>
        <div class="el-upload__text">将文件拖到此处，或<em>点击上传</em></div>
      </el-upload>
    </div>

    <div class="action-bar">
      <el-button type="primary" :loading="!!running" :disabled="!!running" @click="start">
        ▶ 开始整理（收件箱内 {{ inboxFiles.length }} 个文件）
      </el-button>
      <el-button v-if="running" type="danger" plain @click="stop">停止</el-button>
    </div>

    <!-- 任务进度 -->
    <template v-if="running">
      <el-card shadow="hover" class="job-card">
        <div class="job-head">
          <span>任务 #{{ running.id }}</span>
          <el-tag :type="running.status === 'running' ? 'primary' : 'success'">
            {{ running.status === 'running' ? '处理中' : running.status }}
          </el-tag>
        </div>
        <el-progress :percentage="percent()" :status="running.status === 'completed' ? 'success' : undefined" />
        <div class="job-counts">
          <span>已处理 <b>{{ running.processed_files }}/{{ running.total_files }}</b></span>
          <span>成功 <b class="c-green">{{ running.success_count }}</b></span>
          <span>待审核 <b class="c-orange">{{ running.review_count }}</b></span>
          <span>重复 <b class="c-gray">{{ running.duplicate_count }}</b></span>
          <span>失败 <b class="c-red">{{ running.failed_count }}</b></span>
        </div>
      </el-card>
    </template>
  </el-card>
</template>

<style scoped>
.mb16 { margin-bottom: 16px; }
.upload-area { max-width: 560px; }
.action-bar { margin: 16px 0; }
.job-card { margin-top: 8px; }
.job-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  font-weight: 600;
}
.job-counts {
  display: flex;
  gap: 20px;
  margin-top: 12px;
  font-size: 13px;
  color: #606266;
}
.c-green { color: #67c23a; }
.c-orange { color: #e6a23c; }
.c-gray { color: #909399; }
.c-red { color: #f56c6c; }
</style>
