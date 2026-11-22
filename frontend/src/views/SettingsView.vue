<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
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
  adoptRuleCandidate,
  listRuleCandidates,
  rejectRuleCandidate,
  updateFilenameRule,
  updateSettings,
  listExcelSources,
  uploadExcelSource,
  updateExcelSource,
  updateExcelSheet,
  deleteExcelSource,
  testExcelMatch,
  type ExcelSource,
  type CategoryNode,
  type FilenameRule,
  type RuleCandidate,
  type Settings,
  type BackupInfo,
  createBackup,
  deleteBackup,
  downloadBackup,
  listBackups,
  restoreBackup,
  listArchiveTemplates,
  addArchiveTemplate,
  deleteArchiveTemplate,
  type ArchiveTemplateItem,
  getWatchStatus,
  type WatchStatus,
  testOCRHealth,
  type OCRHealth,
  listExtensions,
  installExtension,
  getExtensionProgress,
  uninstallExtension,
  type ExtensionItem,
  getVersion,
  getUpdateProgress,
  startUpdate,
  type VersionInfo,
} from '@/api'

const form = ref<Settings | null>(null)
const ocrTesting = ref(false)
const ocrHealth = ref<OCRHealth | null>(null)

async function testOcr() {
  ocrTesting.value = true
  try {
    ocrHealth.value = await testOCRHealth()
    if (ocrHealth.value.reachable && ocrHealth.value.model_available) {
      ElMessage.success('OCR 端点可达，模型已就绪')
    } else if (ocrHealth.value.reachable) {
      ElMessage.warning('端点可达，但模型名不在服务列表中')
    } else {
      ElMessage.error(ocrHealth.value.error || 'OCR 端点不可达')
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '连通性检测失败')
  } finally {
    ocrTesting.value = false
  }
}

const templatePresets = ref<ArchiveTemplateItem[]>([])
const customTemplate = ref('')
const newTplValue = ref('')
const newTplLabel = ref('')
async function loadTemplatePresets() {
  try {
    const res = await listArchiveTemplates()
    templatePresets.value = res.templates
  } catch {
    templatePresets.value = []
  }
}
async function onAddTemplate() {
  const v = newTplValue.value.trim()
  if (!v) {
    ElMessage.warning('请输入模板，如 category/customer/contract_no')
    return
  }
  await addArchiveTemplate({ value: v, label: newTplLabel.value.trim() || v })
  newTplValue.value = ''
  newTplLabel.value = ''
  await loadTemplatePresets()
  ElMessage.success('已添加模板预设')
}
async function onDeleteTemplate(t: ArchiveTemplateItem) {
  await ElMessageBox.confirm(`删除模板「${t.label}」？`, '删除确认', { type: 'warning' })
  await deleteArchiveTemplate(t.value)
  await loadTemplatePresets()
  ElMessage.success('已删除')
}
function effectiveTemplate(): string {
  if (form.value?.archive_template === '__custom__') return customTemplate.value.trim() || 'category/year/month'
  return form.value?.archive_template || 'category/year/month'
}
function isKnownPreset(tpl: string): boolean {
  return templatePresets.value.some((t) => t.value === tpl)
}
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
const fnCandidates = ref<RuleCandidate[]>([])
const flatCats = ref<CategoryNode[]>([])
const fnForm = ref({ pattern: '', category_id: 0, note: '' })
const fnLoading = ref(false)


// 邮箱接收状态
const emailStatus = ref<{ running: boolean; enabled: boolean; last_check?: string; last_error?: string | null; last_count?: number } | null>(null)
const emailChecking = ref(false)

// 目录自动监控状态
const watchStatus = ref<WatchStatus | null>(null)
// 编辑中的监控目录列表（保存时才提交）
const watchDirsEdit = ref<string[]>([])

async function loadWatchStatus() {
  try {
    watchStatus.value = await getWatchStatus()
  } catch {
    /* 忽略 */
  }
}

function addWatchDir() {
  watchDirsEdit.value.push('')
}

function removeWatchDir(idx: number) {
  watchDirsEdit.value.splice(idx, 1)
}

function watchDirStatusText(dir: string): string {
  const s = watchStatus.value?.dirs?.find((d) => d.dir === dir)
  if (!s) return ''
  const parts: string[] = []
  if (s.last_scan_at) parts.push(`上次扫描 ${s.last_scan_at}`)
  if (s.found_files) parts.push(`发现新文件 ${s.found_files} 个`)
  if (s.last_job_id) parts.push(`最近触发任务 #${s.last_job_id}`)
  return parts.join(' · ')
}

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
  await loadTemplatePresets()
  const savedTpl = form.value?.archive_template || ''
  customTemplate.value = savedTpl && !isKnownPreset(savedTpl) ? savedTpl : ''
  aiApiKey.value = ''
  accessPassword.value = ''
  emailPassword.value = ''
  watchDirsEdit.value = [...(form.value?.watch_dirs || [])]
  loaded.value = true
  await loadFnRules()
  await loadEmailStatus()
  await loadWatchStatus()
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
      ocr_provider: form.value.ocr_provider,
      ocr_base_url: form.value.ocr_base_url,
      ocr_model: form.value.ocr_model,
      ocr_timeout: form.value.ocr_timeout,
      ocr_kie_enabled: form.value.ocr_kie_enabled,
      ocr_fallback: form.value.ocr_fallback,
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
      archive_template: effectiveTemplate(),
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
    // 目录自动监控
    data.watch_enabled = form.value.watch_enabled
    data.watch_dirs = watchDirsEdit.value.map((d) => d.trim()).filter(Boolean)
    data.watch_interval = form.value.watch_interval
    // 盖章检测
    data.seal_detect_enabled = form.value.seal_detect_enabled
    await updateSettings(data)
    aiApiKey.value = ''
    accessPassword.value = ''
    emailPassword.value = ''
    ElMessage.success('设置已保存')
    await loadWatchStatus()
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
  // 候选规则独立加载：失败不影响规则表
  try {
    fnCandidates.value = await listRuleCandidates('pending')
  } catch {
    fnCandidates.value = []
  }
}

async function adoptCandidate(c: RuleCandidate) {
  await ElMessageBox.confirm(
    `采纳后，文件名含「${c.keyword}」的文件将直接归档到「${c.category_path}」，不再解析内容。确定采纳？`,
    '采纳候选规则',
    { type: 'warning' },
  )
  await adoptRuleCandidate(c.id)
  ElMessage.success(`已采纳：${c.keyword} → ${c.category_path}`)
  await loadFnRules()
}

async function rejectCandidate(c: RuleCandidate) {
  await rejectRuleCandidate(c.id)
  fnCandidates.value = fnCandidates.value.filter((x) => x.id !== c.id)
  ElMessage.success('已忽略该候选')
}

function candidateHitText(c: RuleCandidate): string {
  const off = c.hit_total - c.hit_target
  if (off <= 0) return `当前库命中 ${c.hit_total} 份，均已在目标分类`
  return `当前库命中 ${c.hit_total} 份，其中 ${off} 份现在不在该分类（采纳后会改归到这里）`
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

// ---- Excel 登记表（权威数据源） ----
const excelSources = ref<ExcelSource[]>([])
const excelLoading = ref(false)
const excelTestInput = ref('')
const excelTestResult = ref<any>(null)
const excelTestSource = ref<number | null>(null)

async function loadExcelSources() {
  excelLoading.value = true
  try {
    const r = await listExcelSources()
    excelSources.value = r.items || []
  } catch {
    excelSources.value = []
  } finally {
    excelLoading.value = false
  }
}

async function onUploadExcel(file: File) {
  await uploadExcelSource(file)
  ElMessage.success('Excel 登记表已上传并解析')
  await loadExcelSources()
}

function beforeExcelUpload(file: File) {
  const ok = /\.(xlsx|xls|xlsm)$/i.test(file.name)
  if (!ok) ElMessage.error('仅支持 .xlsx / .xls / .xlsm 文件')
  return ok
}

async function toggleExcelSource(src: ExcelSource, v: boolean) {
  await updateExcelSource(src.id, { enabled: v })
  await loadExcelSources()
}

async function toggleExcelSheet(src: ExcelSource, sheetId: number, v: boolean) {
  await updateExcelSheet(src.id, sheetId, { enabled: v })
  await loadExcelSources()
}

async function removeExcelSource(src: ExcelSource) {
  await ElMessageBox.confirm(`删除数据源「${src.name}」？已归档文档不受影响。`, '删除确认', { type: 'warning' })
  await deleteExcelSource(src.id)
  ElMessage.success('已删除')
  await loadExcelSources()
}

async function runExcelTest(src: ExcelSource) {
  excelTestSource.value = src.id
  excelTestResult.value = null
  const r = await testExcelMatch(src.id, { contract_no: excelTestInput.value })
  excelTestResult.value = r.match
}

// ---------- 版本更新 ----------
const versionInfo = ref<VersionInfo | null>(null)
const versionLoading = ref(false)
const updState = ref({ phase: 'idle', progress: 0, message: '', error: '' })
let updTimer: number | null = null

async function loadVersion(refresh = false) {
  versionLoading.value = true
  try {
    versionInfo.value = await getVersion(refresh)
  } catch {
    // 拦截器已提示
  } finally {
    versionLoading.value = false
  }
}

async function onCheckUpdate() {
  await loadVersion(true)
  if (!versionInfo.value?.supported) {
    ElMessage.info(versionInfo.value?.message || '当前模式不支持自动更新')
  } else if (versionInfo.value?.has_update) {
    ElMessage.success('发现新版本')
  } else {
    ElMessage.success('已是最新版本')
  }
}

function stopUpdPolling() {
  if (updTimer) {
    window.clearInterval(updTimer)
    updTimer = null
  }
}

function startUpdPolling() {
  stopUpdPolling()
  updTimer = window.setInterval(async () => {
    try {
      const r = await getUpdateProgress()
      updState.value = r.state
      if (r.state.phase === 'error') {
        stopUpdPolling()
        ElMessage.error(r.state.error || '更新失败')
      }
    } catch {
      stopUpdPolling()
    }
  }, 800)
}

async function onApplyUpdate() {
  const size = Math.round((versionInfo.value?.latest?.size || 0) / 1048576)
  try {
    await ElMessageBox.confirm(
      `将下载约 ${size} MB 的新版本安装包，下载完成后自动静默安装并重启应用。更新期间请勿关闭程序，是否继续？`,
      '更新到最新版本',
      { type: 'warning', confirmButtonText: '立即更新' },
    )
  } catch {
    return
  }
  const r = await startUpdate()
  if (!r.started) {
    ElMessage.warning(r.reason || '无法开始更新')
    return
  }
  updState.value = { phase: 'downloading', progress: 0, message: '准备下载...', error: '' }
  startUpdPolling()
}

// ---------- 功能扩展包（OCR / OpenCV 按需下载） ----------
const extensions = ref<ExtensionItem[]>([])
const extLoading = ref(false)
const extDir = ref('')
let extTimer: number | null = null

async function loadExtensions() {
  extLoading.value = true
  try {
    const r = await listExtensions()
    extensions.value = r.items
    extDir.value = r.extensions_dir
  } catch {
    // http 拦截器已提示
  } finally {
    extLoading.value = false
  }
}

function extBusy(e: ExtensionItem) {
  return e.state.phase === 'downloading' || e.state.phase === 'installing'
}

function stopExtPolling() {
  if (extTimer) {
    window.clearInterval(extTimer)
    extTimer = null
  }
}

function startExtPolling(name: string) {
  stopExtPolling()
  extTimer = window.setInterval(async () => {
    try {
      const r = await getExtensionProgress(name)
      const item = extensions.value.find((x) => x.name === name)
      if (item) item.state = r.state
      if (r.state.phase === 'done' || r.state.phase === 'error') {
        stopExtPolling()
        await loadExtensions()
        if (r.state.phase === 'done') ElMessage.success('扩展包安装完成，重启应用后生效')
        else ElMessage.error(r.state.error || '安装失败')
      }
    } catch {
      stopExtPolling()
    }
  }, 800)
}

async function onInstallExt(e: ExtensionItem) {
  const size = Math.round((e.remote_size || e.size_hint) / 1048576)
  try {
    await ElMessageBox.confirm(
      `将下载约 ${size} MB 的扩展包（OCR 识别 + OpenCV 盖章检测），安装完成后需重启应用才生效。下载期间其他功能可正常使用。`,
      e.installed ? '重新安装扩展包' : '安装功能扩展',
      { type: 'info', confirmButtonText: '开始下载' },
    )
  } catch {
    return
  }
  const r = await installExtension(e.name)
  if (!r.started) {
    ElMessage.warning(r.reason || '已有安装任务在进行')
    return
  }
  ElMessage.success('已开始下载，请稍候')
  startExtPolling(e.name)
}

async function onUninstallExt(e: ExtensionItem) {
  try {
    await ElMessageBox.confirm(
      '卸载后，扫描件文字识别与图片盖章检测将不可用，其余功能不受影响。是否继续？',
      '卸载扩展包',
      { type: 'warning', confirmButtonText: '卸载' },
    )
  } catch {
    return
  }
  const r = await uninstallExtension(e.name)
  if (r.pending) ElMessage.warning(r.reason || '文件被占用，重启应用后自动删除')
  else ElMessage.success('已卸载')
  await loadExtensions()
}

onMounted(() => { load(); loadExcelSources(); loadExtensions(); loadVersion() })
onUnmounted(() => { stopExtPolling(); stopUpdPolling() })
</script>

<template>
  <div v-if="loaded && form" class="settings-page">
    <el-card shadow="never" class="mb16">
      <template #header>
        <span>版本更新</span>
        <el-button link size="small" style="margin-left: 8px" @click="onCheckUpdate">检查更新</el-button>
      </template>
      <div v-loading="versionLoading">
        <div v-if="!versionInfo" class="gray small">正在读取版本信息...</div>
        <div v-else-if="!versionInfo.supported" class="gray small">{{ versionInfo.message }}</div>
        <div v-else>
          <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap">
            <span>当前构建 <code>{{ versionInfo.current_build || '未知' }}</code></span>
            <el-tag
              :type="versionInfo.has_update ? 'warning' : 'success'"
              size="small"
            >{{ versionInfo.has_update ? `有新版本 ${versionInfo.latest?.build}` : '已是最新版本' }}</el-tag>
          </div>
          <div
            v-if="versionInfo.has_update && versionInfo.latest"
            class="gray small"
            style="margin-top: 6px; line-height: 1.7"
          >
            {{ versionInfo.latest.notes || '包含功能改进与问题修复。' }}<br />
            安装包约 {{ Math.round(versionInfo.latest.size / 1048576) }} MB
          </div>
          <div v-if="updState.phase === 'downloading' || updState.phase === 'launching'" style="margin-top: 10px">
            <el-progress :percentage="Math.round(updState.progress)" />
            <div class="gray small" style="margin-top: 4px">{{ updState.message }}</div>
          </div>
          <div
            v-else
            style="margin-top: 10px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap"
          >
            <el-button
              v-if="versionInfo.has_update"
              type="primary"
              size="small"
              @click="onApplyUpdate"
            >立即更新</el-button>
            <span v-if="versionInfo.has_update" class="gray small">下载完成后会自动安装并重启，无需手动操作</span>
          </div>
        </div>
      </div>
    </el-card>

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
      <template #header>
        <span>功能扩展</span>
        <el-button link size="small" style="margin-left: 8px" @click="loadExtensions">刷新</el-button>
        <span class="gray small" style="margin-left: 8px">按需下载，主程序保持轻量</span>
      </template>
      <div v-loading="extLoading">
        <div
          v-for="e in extensions"
          :key="e.name"
          style="border: 1px solid #ebeef5; border-radius: 6px; padding: 12px 14px; margin-bottom: 10px"
        >
          <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
            <strong>{{ e.title }}</strong>
            <el-tag v-if="e.installed" type="success" size="small">已安装 {{ e.version }}</el-tag>
            <el-tag v-else type="info" size="small">未安装</el-tag>
            <el-tag v-if="e.requires_restart" type="warning" size="small">重启后生效</el-tag>
            <el-tag v-if="e.update_available" size="small">有新版本 {{ e.remote_version }}</el-tag>
            <el-tag v-if="e.pending_delete" type="danger" size="small">重启后删除</el-tag>
          </div>
          <div class="gray small" style="margin-top: 6px; line-height: 1.7">
            {{ e.summary }}<br />
            {{ e.detail }}
          </div>
          <div v-if="extBusy(e)" style="margin-top: 8px">
            <el-progress
              :percentage="Math.round(e.state.progress)"
              :status="e.state.phase === 'error' ? 'exception' : undefined"
            />
            <div class="gray small" style="margin-top: 4px">{{ e.state.message }}</div>
          </div>
          <div v-else style="margin-top: 10px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap">
            <el-button v-if="!e.installed" type="primary" size="small" @click="onInstallExt(e)">
              一键下载安装（约 {{ Math.round((e.remote_size || e.size_hint) / 1048576) }} MB）
            </el-button>
            <el-button v-else size="small" @click="onInstallExt(e)">
              {{ e.update_available ? '更新到最新版' : '重新安装' }}
            </el-button>
            <el-button v-if="e.installed" size="small" type="danger" plain @click="onUninstallExt(e)">
              卸载
            </el-button>
            <span class="gray small">
              {{ e.installed ? `占用 ${(e.size / 1048576).toFixed(0)} MB` : e.remote_ok ? '可从仓库下载' : '清单读取失败，检查网络后点刷新' }}
            </span>
            <span v-if="e.installed" class="gray small">
              本进程状态：OCR {{ e.runtime.ocr ? '✓ 可用' : '✗ 未加载' }}　盖章检测
              {{ e.runtime.seal ? '✓ 可用' : '✗ 未加载' }}
            </span>
          </div>
        </div>
        <div class="gray small">安装位置：{{ extDir }}</div>
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
        <el-form-item label="归档目录结构">
          <el-select v-model="form.archive_template" style="width: 100%">
            <el-option
              v-for="t in templatePresets"
              :key="t.value"
              :label="t.label"
              :value="t.value"
            />
            <el-option label="自定义（用 / 连接段）" value="__custom__" />
          </el-select>
          <div style="display: flex; gap: 8px; margin-top: 6px">
            <el-input v-model="newTplValue" placeholder="新增模板，如 category/customer/year" size="small" style="flex: 1" />
            <el-input v-model="newTplLabel" placeholder="显示名（可选）" size="small" style="width: 180px" />
            <el-button size="small" type="primary" @click="onAddTemplate">添加</el-button>
          </div>
          <div v-if="templatePresets.length" class="gray small" style="display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px">
            <el-tag
              v-for="t in templatePresets"
              :key="t.value"
              closable
              size="small"
              @close="onDeleteTemplate(t)"
            >{{ t.label }}</el-tag>
          </div>
          <div class="gray small" style="line-height: 1.6; margin-top: 4px">
            归档路径 = 文档根目录 / 分类 / 后续各段。可用段：<code>category</code>（分类，固定首位）、
            <code>year</code> / <code>month</code> / <code>day</code> / <code>date</code>（日期，来自识别日期），
            以及业务字段名如 <code>customer</code>（客户）、<code>contract_no</code>（合同号）、
            <code>ship_name</code>（船名）等。字段缺失时该段自动跳过，不留空目录。
          </div>
          <el-input
            v-if="form.archive_template === '__custom__'"
            v-model="customTemplate"
            class="mt8"
            placeholder="如 category/customer/contract_no"
          />
          <el-input
            v-else
            class="mt8"
            :model-value="form.archive_template"
            disabled
          />
          <div class="gray small" style="margin-top: 4px">仅影响之后归档的新文件，已归档文件路径不变。</div>
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
            启动窗口会显示当前访问地址（端口由程序自动分配，不再固定 8000）。手机与电脑连同一 WiFi 后打开该地址即可上传归档；
            如需开放局域网，在配置中设置 <code>SDO_HOST=0.0.0.0</code> 并同时设置访问密码。
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
      <template #header><span>目录自动监控（主动整理：放进目录即自动归档）</span></template>
      <div class="gray small mb8">
        设置一个或多个目录后，系统后台定时扫描；发现新文件自动触发识别归档，无需打开网页操作。
        监控线程只"发现"不移动文件——归档后文件会自动移入文档根目录；待人工确认的文件保留在原处等待审核。
      </div>
      <el-form label-width="120px">
        <el-form-item label="启用监控">
          <el-switch v-model="form.watch_enabled" />
          <span class="gray small"> 开启后后台每 {{ form.watch_interval }} 秒扫描一次</span>
        </el-form-item>
        <el-form-item label="监控目录">
          <div style="width: 100%">
            <div v-for="(_, idx) in watchDirsEdit" :key="idx" class="watch-dir-row">
              <el-input v-model="watchDirsEdit[idx]" placeholder="目录绝对路径，如 D:\共享文件夹" style="flex: 1" />
              <el-button type="danger" link @click="removeWatchDir(idx)">删除</el-button>
            </div>
            <el-button type="primary" plain size="small" @click="addWatchDir">+ 添加监控目录</el-button>
            <div class="gray small" style="margin-top: 4px">不能指向归档目录 / 回收站目录（防循环扫描）。目录不存在或无权限时状态栏会提示，不会影响服务。</div>
          </div>
        </el-form-item>
        <el-form-item label="扫描间隔（秒）">
          <el-input-number v-model="form.watch_interval" :min="10" :max="3600" :step="10" />
        </el-form-item>
        <el-form-item label="盖章检测">
          <el-switch v-model="form.seal_detect_enabled" />
          <span class="gray small"> 自动标注文档是否已盖章、章数（本地图像算法，不消耗 AI）</span>
        </el-form-item>
      </el-form>

      <el-divider />
      <div class="email-status">
        <el-tag :type="watchStatus?.running ? 'success' : 'info'" size="small">
          {{ watchStatus?.running ? '监控运行中' : '监控未运行' }}
        </el-tag>
        <el-tag v-if="watchStatus?.last_scan_at" type="info" size="small" class="ml8">
          最近扫描：{{ watchStatus.last_scan_at }}
        </el-tag>
        <el-tag v-if="watchStatus?.dirs?.length" type="info" size="small" class="ml8">
          监控 {{ watchStatus.dirs.length }} 个目录
        </el-tag>
        <div v-for="d in watchStatus?.dirs || []" :key="d.dir" class="watch-dir-status">
          <span class="watch-dir-name">{{ d.dir }}</span>
          <span class="gray small">{{ watchDirStatusText(d.dir) }}</span>
          <el-alert v-if="d.last_error" type="error" :closable="false" show-icon class="mt4"
            :title="'目录异常：' + d.last_error" />
        </div>
      </div>
    </el-card>

    <el-card shadow="never" class="mb16">
      <template #header><span>Excel 登记表（权威数据源：命中后用台账字段命名归档）</span></template>
      <div class="gray small mb8">
        上传你的业务登记台账（合同 / 结算 / 收付款等）。系统 OCR 识别出合同号后自动在台账中匹配，
        命中则用台账中的船名、物料、金额等字段覆盖识别结果，命名更准（纯本地，不消耗 AI）。
        多 sheet 的台账会自动解析，可单独启用 / 停用每个 sheet。
      </div>
      <el-upload :show-file-list="false" :before-upload="beforeExcelUpload" :http-request="(opt: any) => onUploadExcel(opt.file)">
        <el-button type="primary" :loading="excelLoading">上传 Excel 登记表</el-button>
      </el-upload>

      <div v-if="!excelSources.length" class="gray small mt8">尚未上传登记表</div>

      <div v-for="src in excelSources" :key="src.id" class="excel-source mb8">
        <div class="excel-source-head">
          <el-tag type="info" size="small">{{ src.name }}</el-tag>
          <span class="gray small" style="margin:0 4px">启用</span>
          <el-switch :model-value="src.enabled" size="small" @change="(v: boolean) => toggleExcelSource(src, v)" />
          <el-button type="danger" link size="small" @click="removeExcelSource(src)">删除</el-button>
        </div>
        <div v-for="sh in src.sheets" :key="sh.id" class="excel-sheet">
          <span class="sheet-name">{{ sh.sheet_name }}</span>
          <el-switch :model-value="sh.enabled" size="small" @change="(v: boolean) => toggleExcelSheet(src, sh.id, v)" />
          <span v-if="sh.key_column" class="gray small" style="margin-left:8px">主键列：{{ sh.key_column }}</span>
          <div class="gray small" style="font-size:12px;margin-top:2px">
            映射：<span v-for="(col, std) in sh.column_map" :key="std" class="map-chip">{{ std }}→{{ col }}</span>
          </div>
        </div>
        <div class="excel-test" style="margin-top:6px">
          <el-input v-model="excelTestInput" placeholder="输入合同号测试匹配（如 SJWLXS(DD)-2026-YC0453）" size="small" style="width:340px;display:inline-block" />
          <el-button size="small" @click="runExcelTest(src)">测试匹配</el-button>
        </div>
        <div v-if="excelTestSource === src.id && excelTestResult" class="rel-result">
          <el-tag :type="excelTestResult.matched ? 'success' : 'info'" size="small">
            {{ excelTestResult.matched ? '命中：' + excelTestResult.sheet_name + '（' + excelTestResult.key + '）' : '未命中' }}
          </el-tag>
          <div v-if="excelTestResult.matched" class="gray small" style="font-size:12px;word-break:break-all">{{ JSON.stringify(excelTestResult.fields) }}</div>
        </div>
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
          <el-input v-model="relocateSearchRoot" placeholder="文件移动到的文件夹；留空则扫描系统文档目录和收件箱" />
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

      <div v-if="fnCandidates.length" class="cand-block">
        <div class="cand-head">
          <el-tag type="warning" size="small" effect="dark">待采纳</el-tag>
          <span class="cand-title">审核改判发现的候选规则（{{ fnCandidates.length }} 条）</span>
          <span class="gray small">采纳前请核对命中数量：采纳后同前缀文件会直接归档，不再走识别</span>
        </div>
        <el-table :data="fnCandidates" size="small" style="width: 100%">
          <el-table-column label="文件名前缀" width="130">
            <template #default="{ row }"><code>{{ row.keyword }}</code></template>
          </el-table-column>
          <el-table-column prop="category_path" label="归档到" min-width="160" />
          <el-table-column label="影响范围" min-width="280">
            <template #default="{ row }">
              <span :class="row.hit_total - row.hit_target > 0 ? 'warn-text' : 'gray'">
                {{ candidateHitText(row) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="130">
            <template #default="{ row }">
              <el-button link type="primary" @click="adoptCandidate(row)">采纳</el-button>
              <el-button link type="danger" @click="rejectCandidate(row)">忽略</el-button>
            </template>
          </el-table-column>
        </el-table>
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
          <div class="gray small">扫描件 / 图片文字识别</div>
        </el-form-item>
        <el-form-item label="OCR 引擎">
          <el-select v-model="form.ocr_provider" style="width: 260px">
            <el-option label="RapidOCR（本地，快，默认）" value="rapidocr" />
            <el-option label="PaddleOCR（本地，重）" value="paddleocr" />
            <el-option label="Xiaomi-OCR-0（本地，准但慢）" value="xiaomi" />
          </el-select>
          <el-button size="small" style="margin-left: 8px" :loading="ocrTesting" @click="testOcr">
            连通性检测
          </el-button>
          <div v-if="ocrHealth" class="gray small">
            <template v-if="ocrHealth.reachable && ocrHealth.model_available">
              ✓ 端点可达，模型「{{ ocrHealth.model }}」已就绪
            </template>
            <template v-else-if="ocrHealth.reachable">
              ⚠ 端点可达，但模型名不在列表中：{{ ocrHealth.models.join('、') || '（空）' }}
            </template>
            <template v-else>
              ✗ 不可达：{{ ocrHealth.error || '未知错误' }}
            </template>
          </div>
        </el-form-item>
        <template v-if="form.ocr_provider === 'xiaomi'">
          <el-form-item label="推理端点">
            <el-input v-model="form.ocr_base_url" placeholder="http://127.0.0.1:11434/v1" />
          </el-form-item>
          <el-form-item label="模型名">
            <el-input v-model="form.ocr_model" placeholder="如 longwayxu/xiaomi-ocr-0:bf16" />
          </el-form-item>
          <el-form-item label="超时（秒）">
            <el-input-number v-model="form.ocr_timeout" :min="30" :max="600" :step="30" />
            <span class="gray small"> CPU 单页约 50 秒，建议 180</span>
          </el-form-item>
          <el-form-item label="视觉 KIE 兜底">
            <el-switch v-model="form.ocr_kie_enabled" />
            <div class="gray small">
              扫描件缺关键字段（金额/合同号/发票号）或字段置信度偏低时，让模型直读原图补字段。
              单页约 50 秒，只在触发条件下执行。
            </div>
          </el-form-item>
          <el-form-item label="失败回退">
            <el-switch v-model="form.ocr_fallback" />
            <div class="gray small">模型服务不可用时自动回退 RapidOCR，避免整理卡住</div>
          </el-form-item>
        </template>
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
.watch-dir-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.watch-dir-status { flex-basis: 100%; margin-top: 4px; }
.watch-dir-name { font-weight: 600; margin-right: 8px; font-size: 13px; }
.mt4 { margin-top: 4px; }
.mr4 { margin-right: 4px; }
.rel-result { margin-top: 8px; }
.excel-source { border: 1px solid #ebeef5; border-radius: 6px; padding: 10px; }
.excel-source-head { display: flex; align-items: center; gap: 8px; }
.excel-sheet { margin-top: 8px; padding: 6px 10px; background: #fafafa; border-radius: 4px; }
.sheet-name { font-weight: 600; margin-right: 8px; }
.map-chip { background: #e6f0ff; color: #3c6ef0; border-radius: 3px; padding: 0 5px; margin-right: 4px; }
.cand-block {
  border: 1px solid #faecd8;
  background: #fdf6ec;
  border-radius: 6px;
  padding: 10px 10px 2px;
  margin-bottom: 12px;
}
.cand-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap; }
.cand-title { font-weight: 600; font-size: 13px; }
.warn-text { color: #e6a23c; }
</style>
