<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { UploadFile, UploadFiles } from 'element-plus'
import {
  cancelJob,
  getJob,
  listDocuments,
  listFilenameRules,
  listRules,
  retryFailed as retryFailedApi,
  simulateRules,
  startProcessing,
  uploadDocument,
  type FilenameRule,
  type ProcessingJob,
  type Rule,
  type SimulateResult,
} from '@/api'

const uploading = ref(false)
const running = ref<ProcessingJob | null>(null)
const inboxFiles = ref<any[]>([])
const pollTimer = ref<number | null>(null)
const lastFailed = ref(0)

// ---------- 规则模拟器 ----------
const simOpen = ref(false)
const simLoading = ref(false)
const simRuleType = ref<'filename' | 'keyword'>('filename')
const simRules = ref<(FilenameRule | Rule)[]>([])
const simRuleIds = ref<number[]>([])
const simLimit = ref(200)
const simResult = ref<SimulateResult | null>(null)
const simOnlyWrong = ref(false)

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
  lastFailed.value = job.failed_count
  ElMessage.success(
    `整理完成：成功 ${job.success_count} / 待审核 ${job.review_count} / 重复 ${job.duplicate_count} / 失败 ${job.failed_count}`,
  )
  await loadInbox()
}

async function retryFailedFiles() {
  if (running.value) return
  try {
    const job = await retryFailedApi()
    running.value = job
    ElMessage.success(`已开始重试失败文件（${job.total_files} 个）`)
    if (isFinished(job)) {
      finish(job)
      return
    }
    poll()
  } catch {
    /* 错误已在拦截器提示 */
  }
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

// ---------- 规则模拟器 ----------
async function openSimulator() {
  simOpen.value = true
  simResult.value = null
  simRuleIds.value = []
  await loadSimRules()
}

async function loadSimRules() {
  try {
    if (simRuleType.value === 'filename') {
      simRules.value = await listFilenameRules()
    } else {
      simRules.value = await listRules()
    }
  } catch {
    simRules.value = []
  }
}

function onSimRuleTypeChange() {
  simRuleIds.value = []
  loadSimRules()
}

function simRuleLabel(r: FilenameRule | Rule): string {
  if (simRuleType.value === 'filename') {
    const f = r as FilenameRule
    return `${f.pattern}${f.category_name ? ' → ' + f.category_name : ''}${f.note ? '（' + f.note + '）' : ''}`
  }
  const k = r as Rule
  return `${k.keyword}${k.match_type !== 'contains' ? ' [' + k.match_type + ']' : ''}`
}

async function runSimulate() {
  simLoading.value = true
  simResult.value = null
  try {
    simResult.value = await simulateRules({
      rule_type: simRuleType.value,
      rule_ids: simRuleIds.value.length > 0 ? simRuleIds.value : undefined,
      limit: simLimit.value,
    })
    if (simResult.value.total_tested === 0) {
      ElMessage.info('没有可测试的历史文档（需要有已识别类型的文档）')
    } else if (simResult.value.matched === 0) {
      ElMessage.warning(`规则未命中任何文件（测试 ${simResult.value.total_tested} 份）`)
    }
  } catch {
    /* 错误已在拦截器提示 */
  } finally {
    simLoading.value = false
  }
}

function simDetailRows() {
  if (!simResult.value) return []
  const rows = simResult.value.details
  if (simOnlyWrong.value) return rows.filter((d) => d.matched && d.correct === false)
  return rows
}

onMounted(loadInbox)
onUnmounted(stopPoll)
</script>

<template>
  <el-card shadow="never">
    <template #header>
      <div class="card-head">
        <span>批量整理</span>
        <el-button size="small" type="primary" plain @click="openSimulator">
          <el-icon style="margin-right: 4px"><magic-stick /></el-icon>
          规则模拟器
        </el-button>
      </div>
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
      <el-button v-if="lastFailed > 0 && !running" type="warning" plain @click="retryFailedFiles">
        重试失败（{{ lastFailed }} 个）
      </el-button>
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

  <!-- ============ 规则模拟器弹窗 ============ -->
  <el-dialog v-model="simOpen" title="规则模拟器" width="78%" top="4vh" destroy-on-close>
    <div class="sim-body">
      <!-- 配置区 -->
      <div class="sim-config">
        <el-radio-group v-model="simRuleType" @change="onSimRuleTypeChange">
          <el-radio-button value="filename">文件名规则</el-radio-button>
          <el-radio-button value="keyword">分类关键词规则</el-radio-button>
        </el-radio-group>

        <div class="sim-config-row">
          <span class="sim-config-label">规则范围</span>
          <el-select
            v-model="simRuleIds"
            multiple
            filterable
            collapse-tags
            collapse-tags-tooltip
            clearable
            placeholder="不选 = 全部启用规则"
            style="width: 380px"
          >
            <el-option v-for="r in simRules" :key="r.id" :label="simRuleLabel(r)" :value="r.id" />
          </el-select>
        </div>

        <div class="sim-config-row">
          <span class="sim-config-label">测试数量</span>
          <el-input-number v-model="simLimit" :min="10" :max="1000" :step="50" style="width: 140px" />
          <span class="gray small">份（最近上传的文档优先，含已归档/待审核等所有已识别类型的文件）</span>
        </div>

        <div class="sim-config-row">
          <el-button type="primary" :loading="simLoading" @click="runSimulate">▶ 开始模拟</el-button>
          <span class="gray small">只读测试，不修改任何文件和数据</span>
        </div>
      </div>

      <!-- 结果区 -->
      <template v-if="simResult">
        <div class="sim-stats">
          <div class="stat-card">
            <div class="stat-num">{{ simResult.total_tested }}</div>
            <div class="stat-label">测试文件</div>
          </div>
          <div class="stat-card ok">
            <div class="stat-num">{{ simResult.matched }}</div>
            <div class="stat-label">规则命中</div>
          </div>
          <div class="stat-card warn">
            <div class="stat-num">{{ simResult.misclassified }}</div>
            <div class="stat-label">疑似误判</div>
          </div>
          <div class="stat-card miss">
            <div class="stat-num">{{ simResult.unmatched }}</div>
            <div class="stat-label">未命中</div>
          </div>
          <div class="stat-card blue">
            <div class="stat-num">{{ (simResult.hit_accuracy * 100).toFixed(1) }}%</div>
            <div class="stat-label">命中准确率</div>
          </div>
          <div class="stat-card blue">
            <div class="stat-num">{{ (simResult.coverage * 100).toFixed(1) }}%</div>
            <div class="stat-label">覆盖率</div>
          </div>
        </div>
        <div class="sim-desc gray small">
          命中准确率 = 判对 ÷ 命中（规则判断正确的能力）；覆盖率 = 命中 ÷ 总数（规则覆盖范围）。
          当前测试 {{ simResult.tested_rules }} 条规则。
        </div>

        <div class="sim-detail-head">
          <span>判定明细</span>
          <el-checkbox v-model="simOnlyWrong">只看误判</el-checkbox>
        </div>
        <el-table :data="simDetailRows()" size="small" max-height="360" style="width: 100%">
          <el-table-column type="index" width="45" />
          <el-table-column prop="filename" label="文件名" min-width="200" show-overflow-tooltip />
          <el-table-column prop="actual_type" label="真实类型" width="120" />
          <el-table-column prop="predicted_type" label="规则判定" width="120">
            <template #default="{ row }">
              <span v-if="row.matched">{{ row.predicted_type }}</span>
              <span v-else class="gray">— 未命中 —</span>
            </template>
          </el-table-column>
          <el-table-column label="结果" width="90">
            <template #default="{ row }">
              <el-tag v-if="row.correct === true" type="success" size="small">✓ 正确</el-tag>
              <el-tag v-else-if="row.correct === false" type="danger" size="small">✗ 误判</el-tag>
              <el-tag v-else type="info" size="small">未命中</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="keywords" label="命中关键词" min-width="140">
            <template #default="{ row }">
              <el-tag v-for="k in row.keywords" :key="k" size="small" class="kw-tag">{{ k }}</el-tag>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="simDetailRows().length === 0" description="无明细" :image-size="60" />
      </template>
    </div>
  </el-dialog>
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

.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

/* 模拟器 */
.sim-body { max-height: 78vh; overflow-y: auto; padding-right: 4px; }
.sim-config {
  background: #fafbfc;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 14px;
}
.sim-config-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.sim-config-label {
  font-size: 13px;
  color: #606266;
  flex-shrink: 0;
}
.sim-stats {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 10px;
  margin: 12px 0 6px;
}
.stat-card {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 12px;
  text-align: center;
}
.stat-num { font-size: 22px; font-weight: 700; color: #303133; }
.stat-label { font-size: 12px; color: #909399; margin-top: 4px; }
.stat-card.ok .stat-num { color: #67c23a; }
.stat-card.warn .stat-num { color: #f56c6c; }
.stat-card.miss .stat-num { color: #e6a23c; }
.stat-card.blue .stat-num { color: #409eff; }
.sim-desc { margin-bottom: 10px; }
.sim-detail-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 14px;
  font-weight: 600;
  margin: 12px 0 8px;
  color: #303133;
}
.kw-tag { margin-right: 4px; }
.gray { color: #909399; }
.small { font-size: 12px; }
</style>
