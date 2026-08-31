<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  checkEmailNow,
  createFilenameRule,
  deleteFilenameRule,
  deleteRecognitionTemplate,
  getEmailStatus,
  getSettings,
  listCategoriesFlat,
  listFilenameRules,
  listRecognitionTemplates,
  updateFilenameRule,
  updateSettings,
  updateRecognitionTemplate,
  type CategoryNode,
  type FilenameRule,
  type RecognitionTemplate,
  type Settings,
} from '@/api'

const form = ref<Settings | null>(null)
const aiApiKey = ref('')
const accessPassword = ref('')
const emailPassword = ref('')
const saving = ref(false)
const loaded = ref(false)

// 文件名规则
const fnRules = ref<FilenameRule[]>([])
const flatCats = ref<CategoryNode[]>([])
const fnForm = ref({ pattern: '', category_id: 0, note: '' })
const fnLoading = ref(false)

// 识别模板（同类文件自动归档）
const templates = ref<RecognitionTemplate[]>([])
const tplLoading = ref(false)

// 邮箱接收状态
const emailStatus = ref<{ running: boolean; enabled: boolean; last_check?: string; last_error?: string | null; last_count?: number } | null>(null)
const emailChecking = ref(false)

async function loadEmailStatus() {
  try {
    emailStatus.value = await getEmailStatus()
  } catch {
    /* 忽略 */
  }
}

async function doCheckEmail() {
  emailChecking.value = true
  try {
    const res = await checkEmailNow()
    emailStatus.value = res.status
    if (res.count > 0) {
      ElMessage.success(`已接收 ${res.count} 个文件并开始识别归档`)
    } else if (res.status?.last_error) {
      ElMessage.error(`收取失败：${res.status.last_error}`)
    } else {
      ElMessage.info('检查完成，没有新文件')
    }
  } finally {
    emailChecking.value = false
  }
}

async function loadTemplates() {
  tplLoading.value = true
  try {
    templates.value = await listRecognitionTemplates()
  } finally {
    tplLoading.value = false
  }
}

async function toggleTemplate(t: RecognitionTemplate) {
  await updateRecognitionTemplate(t.id, { enabled: !t.enabled })
  t.enabled = !t.enabled
  ElMessage.success(t.enabled ? '已启用，同类型文件将自动归档' : '已停用')
}

async function removeTemplate(t: RecognitionTemplate) {
  await ElMessageBox.confirm(`删除「${t.document_type}」模板？删除后该类型文件将重新走人工审核。`, '删除确认', { type: 'warning' })
  await deleteRecognitionTemplate(t.id)
  ElMessage.success('已删除')
  await loadTemplates()
}

async function load() {
  form.value = await getSettings()
  aiApiKey.value = ''
  accessPassword.value = ''
  emailPassword.value = ''
  loaded.value = true
  await loadFnRules()
  await loadTemplates()
  await loadEmailStatus()
}

async function save() {
  if (!form.value) return
  saving.value = true
  try {
    const data: Partial<Settings> = {
      document_root: form.value.document_root,
      inbox_root: form.value.inbox_root,
      ocr_enabled: form.value.ocr_enabled,
      ai_enabled: form.value.ai_enabled,
      ai_provider: form.value.ai_provider,
      ai_base_url: form.value.ai_base_url,
      ai_model: form.value.ai_model,
      ai_timeout: form.value.ai_timeout,
      ai_max_retries: form.value.ai_max_retries,
      ai_max_text_length: form.value.ai_max_text_length,
      auto_archive_threshold: form.value.auto_archive_threshold,
      review_threshold: form.value.review_threshold,
      conf_rule_weight: form.value.conf_rule_weight,
      conf_field_weight: form.value.conf_field_weight,
      conf_keyword_weight: form.value.conf_keyword_weight,
      conf_ai_weight: form.value.conf_ai_weight,
      allow_overwrite: form.value.allow_overwrite,
    }
    if (aiApiKey.value.trim()) {
      data.ai_api_key = aiApiKey.value.trim()
    }
    if (accessPassword.value.trim()) {
      data.access_password = accessPassword.value.trim()
    }
    data.email_enabled = form.value.email_enabled
    data.email_imap_host = form.value.email_imap_host
    data.email_imap_port = form.value.email_imap_port
    data.email_user = form.value.email_user
    data.email_poll_interval = form.value.email_poll_interval
    if (emailPassword.value.trim()) {
      data.email_password = emailPassword.value.trim()
    }
    await updateSettings(data)
    aiApiKey.value = ''
    accessPassword.value = ''
    emailPassword.value = ''
    ElMessage.success('设置已保存')
    // 若刚设置了访问密码，刷新登录态（提示将需要密码）
    if (data.access_password) {
      ElMessage.warning('已设置访问密码，下次打开系统需输入密码')
    }
  } finally {
    saving.value = false
  }
}

// ---------- 文件名规则 ----------
async function loadFnRules() {
  fnLoading.value = true
  try {
    fnRules.value = await listFilenameRules()
    flatCats.value = await listCategoriesFlat()
  } finally {
    fnLoading.value = false
  }
}

function catName(id: number): string {
  const c = flatCats.value.find((x) => x.id === id)
  return c ? c.path : `#${id}`
}

async function addFnRule() {
  if (!fnForm.value.pattern.trim()) {
    ElMessage.warning('请填写文件名前缀/模式')
    return
  }
  if (!fnForm.value.category_id) {
    ElMessage.warning('请选择归档分类')
    return
  }
  await createFilenameRule({
    pattern: fnForm.value.pattern.trim(),
    category_id: fnForm.value.category_id,
    note: fnForm.value.note.trim() || undefined,
  })
  fnForm.value = { pattern: '', category_id: 0, note: '' }
  ElMessage.success('已添加，命中该前缀的文件将直接归档')
  await loadFnRules()
}

async function toggleFnRule(r: FilenameRule) {
  await updateFilenameRule(r.id, { enabled: !r.enabled })
  r.enabled = !r.enabled
}

async function removeFnRule(r: FilenameRule) {
  await ElMessageBox.confirm(`确定删除文件名规则「${r.pattern}」？`, '删除确认', { type: 'warning' })
  await deleteFilenameRule(r.id)
  ElMessage.success('已删除')
  await loadFnRules()
}

onMounted(load)
</script>

<template>
  <div v-if="loaded && form" class="settings-page">
    <el-card shadow="never" class="mb16">
      <template #header><span>目录设置</span></template>
      <el-form label-width="120px">
        <el-form-item label="文档根目录">
          <el-input v-model="form.document_root" />
          <div class="gray small">归档文件将按「分类/年/月」存放于此</div>
        </el-form-item>
        <el-form-item label="待整理目录">
          <el-input v-model="form.inbox_root" />
          <div class="gray small">批量整理时扫描此目录中的文件</div>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" class="mb16">
      <template #header><span>手机接收（局域网网页 + 邮箱自动归档）</span></template>

      <el-divider content-position="left">方式一：局域网网页访问</el-divider>
      <el-form label-width="120px">
        <el-form-item label="访问密码">
          <el-input v-model="accessPassword" type="password" placeholder="留空则不修改；设置后访问需输密码" show-password />
          <div class="gray small">开放局域网后建议设置密码，防止同一 WiFi 下他人访问系统（含 AI Key 配置）</div>
        </el-form-item>
        <el-form-item label="手机访问方式">
          <div class="gray small">
            启动脚本已开放局域网（0.0.0.0）。手机与电脑连同一 WiFi，浏览器打开
            <code>http://电脑IP:8000</code>（启动窗口会显示具体地址），即可上传文件自动归档。
          </div>
        </el-form-item>
      </el-form>

      <el-divider content-position="left">方式二：邮箱接收（不受同一 WiFi 限制，推荐）</el-divider>
      <el-form label-width="120px">
        <el-form-item label="启用邮箱接收">
          <el-switch v-model="form.email_enabled" />
          <span class="gray small"> 开启后后台每 {{ form.email_poll_interval }} 秒检查一次收件箱</span>
        </el-form-item>
        <el-form-item label="IMAP 服务器">
          <el-input v-model="form.email_imap_host" placeholder="如 imap.qq.com / imap.163.com" />
        </el-form-item>
        <el-form-item label="端口">
          <el-input-number v-model="form.email_imap_port" :min="1" :max="65535" />
        </el-form-item>
        <el-form-item label="邮箱账号">
          <el-input v-model="form.email_user" placeholder="如 yourmail@qq.com" />
        </el-form-item>
        <el-form-item label="IMAP 授权码">
          <el-input v-model="emailPassword" type="password" placeholder="授权码（留空则不修改）" show-password />
          <div class="gray small">需在邮箱设置中开启 IMAP 并生成授权码（QQ/163 用授权码而非登录密码）</div>
        </el-form-item>
        <el-form-item label="轮询间隔（秒）">
          <el-input-number v-model="form.email_poll_interval" :min="60" :max="3600" :step="30" />
        </el-form-item>
      </el-form>
      <div class="gray small">
        使用方式：手机把文件作为邮件附件发给 {{ form.email_user || '上述邮箱' }}，系统自动下载到待整理目录并触发识别归档（约在轮询间隔内完成）。
      </div>

      <el-divider />
      <div class="email-status">
        <el-tag :type="emailStatus?.running ? 'success' : 'info'" size="small">
          {{ emailStatus?.running ? '轮询运行中' : '轮询未运行' }}
        </el-tag>
        <el-tag v-if="emailStatus?.last_check" type="info" size="small" class="ml8">
          最近检查：{{ emailStatus.last_check }}
        </el-tag>
        <el-tag v-if="emailStatus?.last_count" type="success" size="small" class="ml8">
          最近新增：{{ emailStatus.last_count }} 个
        </el-tag>
        <div v-if="emailStatus?.last_error" class="email-error">
          <el-alert type="error" :closable="false" show-icon
            :title="'收取失败：' + emailStatus.last_error" />
        </div>
        <el-button type="primary" plain size="small" class="mt8" :loading="emailChecking" @click="doCheckEmail">
          立即检查邮箱
        </el-button>
      </div>
    </el-card>

    <el-card shadow="never" class="mb16">
      <template #header><span>文件名规则（命中即直接归档，不解析内容）</span></template>
      <div class="gray small mb8">
        适用业务编码明确的文件，如 <code>SJWLXS</code>=销售合同、<code>SJWLCG</code>=采购合同。命中后直接归档到对应分类、保持原名，零识别消耗。
      </div>
      <el-table :data="fnRules" size="small" v-loading="fnLoading" style="width: 100%">
        <el-table-column prop="pattern" label="文件名模式" min-width="140" />
        <el-table-column label="归档分类" min-width="180">
          <template #default="{ row }">{{ catName(row.category_id) }}</template>
        </el-table-column>
        <el-table-column prop="note" label="说明" min-width="160" show-overflow-tooltip />
        <el-table-column label="启用" width="70">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" @change="toggleFnRule(row)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="70">
          <template #default="{ row }">
            <el-button link type="danger" @click="removeFnRule(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-form inline class="mt8" @submit.prevent>
        <el-form-item>
          <el-input v-model="fnForm.pattern" placeholder="前缀/模式，如 SJWLXS" style="width: 180px" />
        </el-form-item>
        <el-form-item>
          <el-select v-model="fnForm.category_id" placeholder="选择归档分类" style="width: 200px" filterable>
            <el-option v-for="c in flatCats" :key="c.id" :label="c.path" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-input v-model="fnForm.note" placeholder="说明（可选）" style="width: 160px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="addFnRule">添加</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" class="mb16">
      <template #header><span>已学习的识别模板（同类文件自动归档）</span></template>
      <div class="gray small mb8">
        人工审核确认归档后，系统会记住该类型文件的归档方式；下次再遇同类型且关键字段齐全的文件，自动归档、不进人工审核、不耗 AI。
        可在此停用或删除模板。
      </div>
      <el-table :data="templates" size="small" v-loading="tplLoading" style="width: 100%">
        <el-table-column prop="document_type" label="文档类型" width="120" />
        <el-table-column prop="category_path" label="归档分类" min-width="150" />
        <el-table-column label="关键字段" min-width="200">
          <template #default="{ row }">
            <el-tag v-for="f in row.require_fields" :key="f" size="small" class="mr4">{{ f }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="usage_count" label="已自动归档" width="90" />
        <el-table-column label="启用" width="70">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" @change="toggleTemplate(row)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="70">
          <template #default="{ row }">
            <el-button link type="danger" @click="removeTemplate(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!tplLoading && templates.length === 0" description="暂无模板——确认归档后会在这里自动生成" :image-size="60" />
    </el-card>

    <el-card shadow="never" class="mb16">
      <template #header><span>识别能力</span></template>
      <el-form label-width="120px">
        <el-form-item label="OCR 识别">
          <el-switch v-model="form.ocr_enabled" active-text="启用" />
          <div class="gray small">扫描件 / 图片文字识别（RapidOCR 本地模型）</div>
        </el-form-item>
        <el-form-item label="AI 辅助">
          <el-switch v-model="form.ai_enabled" />
          <el-tag v-if="form.ai_api_key_set" type="success" size="small">已配置</el-tag>
          <el-tag v-else type="info" size="small">未配置 Key，纯规则模式</el-tag>
        </el-form-item>
        <el-form-item label="AI 服务">
          <el-select v-model="form.ai_provider" style="width: 200px">
            <el-option label="OpenAI Compatible" value="openai" />
            <el-option label="DeepSeek" value="deepseek" />
            <el-option label="通义千问 Qwen" value="qwen" />
            <el-option label="Ollama 本地" value="ollama" />
            <el-option label="自定义" value="custom" />
          </el-select>
        </el-form-item>
        <el-form-item label="API 地址">
          <el-input v-model="form.ai_base_url" placeholder="https://api.deepseek.com/v1 或 http://localhost:11434/v1" />
        </el-form-item>
        <el-form-item label="模型">
          <el-input v-model="form.ai_model" placeholder="如 deepseek-chat / qwen2.5" />
        </el-form-item>
        <el-form-item label="AI Key">
          <el-input v-model="aiApiKey" type="password" placeholder="API Key（留空则不修改）" show-password />
          <div class="gray small">Key 保存到后端 .env，界面不回显</div>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" class="mb16">
      <template #header><span>置信度与归档策略</span></template>
      <el-form label-width="160px">
        <el-form-item label="自动归档阈值">
          <el-input-number v-model="form.auto_archive_threshold" :min="0.5" :max="1" :step="0.01" />
          <span class="gray small"> 置信度 ≥ 此值直接归档</span>
        </el-form-item>
        <el-form-item label="人工确认阈值">
          <el-input-number v-model="form.review_threshold" :min="0" :max="1" :step="0.01" />
          <span class="gray small"> 置信度低于此值无法判断</span>
        </el-form-item>
        <el-form-item label="规则权重">
          <el-input-number v-model="form.conf_rule_weight" :min="0" :max="1" :step="0.05" />
        </el-form-item>
        <el-form-item label="字段权重">
          <el-input-number v-model="form.conf_field_weight" :min="0" :max="1" :step="0.05" />
        </el-form-item>
        <el-form-item label="关键词权重">
          <el-input-number v-model="form.conf_keyword_weight" :min="0" :max="1" :step="0.05" />
        </el-form-item>
        <el-form-item label="AI 权重">
          <el-input-number v-model="form.conf_ai_weight" :min="0" :max="1" :step="0.05" />
        </el-form-item>
        <el-form-item label="允许覆盖重名文件">
          <el-switch v-model="form.allow_overwrite" />
          <span class="gray small">（默认关闭，重名自动加 _001）</span>
        </el-form-item>
      </el-form>
    </el-card>

    <el-button type="primary" :loading="saving" @click="save">保存设置</el-button>
  </div>
</template>

<style scoped>
.settings-page {
  max-width: 860px;
}
.mb16 { margin-bottom: 16px; }
.mb8 { margin-bottom: 8px; }
.mt8 { margin-top: 8px; }
.gray { color: #909399; }
.small { font-size: 12px; }
code { background: #f0f2f5; padding: 0 4px; border-radius: 3px; }
.ml8 { margin-left: 8px; }
.email-status { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.email-error { flex-basis: 100%; margin-top: 4px; }
.mr4 { margin-right: 4px; }
</style>
