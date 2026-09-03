<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  checkEmailNow,
  createFilenameRule,
  deleteFilenameRule,
  getConfigCheck,
  getEmailStatus,
  getRelocateStatus,
  getSettings,
  relocateDocuments,
  listCategoriesFlat,
  listFilenameRules,
  updateFilenameRule,
  updateSettings,
  type CategoryNode,
  type FilenameRule,
  type Settings,
  type BackupInfo,
  createBackup,
  deleteBackup,
  downloadBackup,
  listBackups,
  restoreBackup,
} from '@/api'

const form = ref<Settings | null>(null)
const aiApiKey = ref('')
const accessPassword = ref('')
const emailPassword = ref('')
const saving = ref(false)
const loaded = ref(false)

// 配置状态校验
const configCheck = ref<{ summary: string; checks: any[] } | null>(null)
const configCheckLoading = ref(false)

async function loadConfigCheck() {
  configCheckLoading.value = true
  try {
    configCheck.value = await getConfigCheck()
  } catch {
    configCheck.value = null
  } finally {
    configCheckLoading.value = false
  }
}

const configSummaryText = () => {
  if (!configCheck.value) return ''
  const s = configCheck.value.summary
  if (s === 'ok') return '配置正常，可放心使用'
  const w = configCheck.value.checks.filter((c) => c.level === 'warn').length
  const e = configCheck.value.checks.filter((c) => c.level === 'error').length
  if (s === 'error') return `存在 ${e} 项错误、${w} 项警告，部分功能可能不可用`
  return `存在 ${w} 项警告，建议查看处理`
}

function configLevelLabel(l: string): string {
  return { ok: '正常', warn: '警告', error: '错误', info: '提示' }[l] || l
}

// 文件名规则
const fnRules = ref<FilenameRule[]>([])
const flatCats = ref<CategoryNode[]>([])
const fnForm = ref({ pattern: '', category_id: 0, note: '' })
const fnLoading = ref(false)


// 邮箱接收状态
const emailStatus = ref<{ running: boolean; enabled: boolean; last_check?: string; last_error?: string | null; last_count?: number } | null>(null)
const emailChecking = ref(false)

// 备份 / 恢复
const backups = ref<BackupInfo[]>([])
const backupLoading = ref(false)
const backupCreating = ref(false)
const backupRestoring = ref(false)

async function loadBackups() {
  backupLoading.value = true
  try {
    const res = await listBackups()
    backups.value = res.backups
  } finally {
    backupLoading.value = false
  }
}

async function doCreateBackup(includeDocs: boolean) {
  backupCreating.value = true
  try {
    await createBackup(includeDocs)
    ElMessage.success(includeDocs ? '备份完成（已含归档文档）' : '备份完成')
    await loadBackups()
  } finally {
    backupCreating.value = false
  }
}

async function doDeleteBackup(b: BackupInfo) {
  await ElMessageBox.confirm(`删除备份「${b.filename}」？`, '删除确认', { type: 'warning' })
  await deleteBackup(b.filename)
  ElMessage.success('已删除')
  await loadBackups()
}

function doDownloadBackup(b: BackupInfo) {
  downloadBackup(b.filename).catch((e) => ElMessage.error(String(e?.message || e)))
}

function handleRestoreUpload(f: File) {
  if (!f) return
  ElMessageBox.confirm(
    '恢复会覆盖当前全部数据（文档档案与配置），且不可撤销。确定继续吗？',
    '高危操作',
    { type: 'warning', confirmButtonText: '确认恢复', cancelButtonText: '取消' },
  )
    .then(async () => {
      backupRestoring.value = true
      try {
        const res = await restoreBackup(f)
        ElMessage.success(`恢复完成：${res.restored_documents} 份文档`)
        await loadBackups()
        await load()
      } finally {
        backupRestoring.value = false
      }
    })
    .catch(() => {})
}

// 文件关联（重定位）
const relocating = ref(false)
const relocateSearchRoot = ref('')
const relStatus = ref<{ total: number; missing_count: number; missing: { id: number; current_filename: string; path: string }[] } | null>(null)
const relResult = ref<{ relinked: number; failed: number; matched: any[]; unmatched: any[]; error?: string } | null>(null)

async function loadRelocateStatus() {
  try {
    relStatus.value = await getRelocateStatus()
  } catch {
    /* 忽略 */
  }
}

async function doRelocate(byHash: boolean) {
  relocating.value = true
  relResult.value = null
  try {
    const roots = relocateSearchRoot.value.trim() ? [relocateSearchRoot.value.trim()] : []
    const res = await relocateDocuments(roots, byHash)
    relResult.value = res
    if (res.error) {
      ElMessage.warning(res.error)
    } else if (res.relinked > 0) {
      ElMessage.success(`已重新关联 ${res.relinked} 个文件`)
    } else {
      ElMessage.info(`未找到可重新关联的文件${res.failed ? `（仍有 ${res.failed} 个未匹配）` : ''}`)
    }
    await loadRelocateStatus()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '重定位失败')
  } finally {
    relocating.value = false
  }
}

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

async function load() {
  form.value = await getSettings()
  aiApiKey.value = ''
  accessPassword.value = ''
  emailPassword.value = ''
  loaded.value = true
  await loadFnRules()
  await loadEmailStatus()
  await loadRelocateStatus()
  await loadBackups()
  await loadConfigCheck()
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
      <template #header>
        <span>配置状态</span>
        <el-button link size="small" @click="loadConfigCheck" style="margin-left: 8px">刷新</el-button>
      </template>
      <div v-loading="configCheckLoading">
        <el-alert
          v-if="configCheck"
          :type="configCheck.summary === 'ok' ? 'success' : configCheck.summary === 'warn' ? 'warning' : 'error'"
          :title="configSummaryText()"
          :closable="false"
          show-icon
        />
        <el-collapse v-if="configCheck" class="mt8">
          <el-collapse-item :title="`查看明细（${configCheck.checks.length} 项）`">
            <el-table :data="configCheck.checks" size="small" style="width: 100%">
              <el-table-column prop="label" label="项目" width="120" />
              <el-table-column label="状态" width="80">
                <template #default="{ row }">
                  <el-tag
                    :type="row.level === 'ok' ? 'success' : row.level === 'warn' ? 'warning' : row.level === 'error' ? 'danger' : 'info'"
                    size="small"
                  >{{ configLevelLabel(row.level) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="message" label="说明" min-width="240" show-overflow-tooltip />
              <el-table-column prop="detail" label="详情" min-width="160" show-overflow-tooltip />
            </el-table>
          </el-collapse-item>
        </el-collapse>
      </div>
    </el-card>

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
      <template #header><span>文件关联（移动后重新定位）</span></template>
      <div class="gray small mb8">
        归档文件按「分类/年/月」存放在文档根目录。如果你在资源管理器中把已归档文件移动到其他文件夹，
        系统记录不会消失，但原路径会失效、文件打不开。可在此输入文件的新位置，系统按文件名（或文件哈希）
        自动找回并重新关联。
      </div>
      <el-form label-width="120px">
        <el-form-item label="文件状态">
          <el-tag :type="relStatus && relStatus.missing_count > 0 ? 'warning' : 'success'">
            {{ relStatus ? relStatus.total + ' 条记录，' + relStatus.missing_count + ' 个文件路径失效' : '加载中…' }}
          </el-tag>
        </el-form-item>
        <el-form-item label="搜索目录（可选）">
          <el-input v-model="relocateSearchRoot" placeholder="文件移动到的文件夹；留空则仅扫描系统文档根目录" />
          <div class="gray small">例如把「data\documents」整体移到了 D:\我的归档，就填 D:\我的归档</div>
        </el-form-item>
        <el-form-item label="操作">
          <el-button type="primary" :loading="relocating" :disabled="!relStatus || relStatus.missing_count === 0" @click="doRelocate(false)">
            按文件名重新关联
          </el-button>
          <el-button :loading="relocating" :disabled="!relStatus || relStatus.missing_count === 0" @click="doRelocate(true)">
            按文件哈希精确关联（较慢）
          </el-button>
        </el-form-item>
      </el-form>

      <div v-if="relResult" class="rel-result">
        <el-alert
          :type="relResult.failed === 0 ? 'success' : 'warning'"
          :closable="false"
          show-icon
          :title="`关联完成：成功 ${relResult.relinked} 个，未匹配 ${relResult.failed} 个`"
        />
        <div v-if="relResult.unmatched.length" class="mt8">
          <div class="gray small mb8">以下文件未找到，请确认搜索目录正确：</div>
          <el-table :data="relResult.unmatched" size="small" style="width: 100%">
            <el-table-column prop="current_filename" label="文件名" min-width="200" show-overflow-tooltip />
            <el-table-column prop="path" label="原路径" min-width="220" show-overflow-tooltip />
          </el-table>
        </div>
      </div>
    </el-card>

    <el-card shadow="never" class="mb16">
      <template #header><span>备份与恢复</span></template>
      <div class="gray small mb8">
        备份包含数据库（文档档案/字段/日志/模板/规则）与配置（.env）。可勾选同时备份归档文档。恢复会覆盖当前数据，请谨慎操作。
      </div>
      <div class="mb16">
        <el-button type="primary" :loading="backupCreating" @click="doCreateBackup(false)">创建备份（仅数据+配置）</el-button>
        <el-button :loading="backupCreating" @click="doCreateBackup(true)">创建备份（含归档文档）</el-button>
        <el-upload
          :show-file-list="false"
          :auto-upload="false"
          accept=".zip"
          :on-change="(f: any) => { if (f?.raw) handleRestoreUpload(f.raw as File) }"
          style="display: inline-block; margin-left: 12px"
        >
          <el-button type="danger" plain :loading="backupRestoring">从备份恢复…</el-button>
        </el-upload>
      </div>
      <el-table :data="backups" size="small" v-loading="backupLoading" empty-text="暂无备份" style="width: 100%">
        <el-table-column prop="filename" label="备份文件" min-width="220" show-overflow-tooltip />
        <el-table-column label="创建时间" min-width="160">
          <template #default="{ row }">{{ new Date(row.created_at).toLocaleString() }}</template>
        </el-table-column>
        <el-table-column label="大小" width="100">
          <template #default="{ row }">{{ (row.size / 1024 / 1024).toFixed(2) }} MB</template>
        </el-table-column>
        <el-table-column label="文档数" width="90">
          <template #default="{ row }">{{ row.documents_count }}</template>
        </el-table-column>
        <el-table-column label="含文档" width="90">
          <template #default="{ row }">{{ row.include_documents ? '是' : '否' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="140">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="doDownloadBackup(row)">下载</el-button>
            <el-button link type="danger" size="small" @click="doDeleteBackup(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
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
.rel-result { margin-top: 8px; }
</style>
