<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createFilenameRule,
  deleteFilenameRule,
  deleteRecognitionTemplate,
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
  loaded.value = true
  await loadFnRules()
  await loadTemplates()
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
    await updateSettings(data)
    aiApiKey.value = ''
    ElMessage.success('设置已保存')
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
.mr4 { margin-right: 4px; }
</style>
