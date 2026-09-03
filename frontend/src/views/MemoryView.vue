<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createSample,
  deleteRecognitionTemplate,
  deleteSample,
  listCategories,
  listRecognitionTemplates,
  listSamples,
  updateRecognitionTemplate,
  updateSample,
  type CategoryNode,
  type DocumentSampleItem,
  type RecognitionTemplate,
} from '@/api'

// ---------- 格式样本（主动喂样本，版式记忆） ----------
const samples = ref<DocumentSampleItem[]>([])
const sampleLoading = ref(false)
const uploading = ref(false)
const sampleFile = ref<File | null>(null)
const sampleForm = ref({ document_type: '', category_path: '' })
const catPaths = ref<string[]>([])
const featureDialog = ref(false)
const featureData = ref<DocumentSampleItem | null>(null)

// ---------- 已学习的识别模板（确认归档自动积累，字段记忆） ----------
const templates = ref<RecognitionTemplate[]>([])
const tplLoading = ref(false)

function collectPaths(nodes: CategoryNode[]): string[] {
  const out: string[] = []
  for (const n of nodes) {
    out.push(n.path)
    if (n.children?.length) out.push(...collectPaths(n.children))
  }
  return out
}

async function loadSamples() {
  sampleLoading.value = true
  try {
    samples.value = await listSamples()
  } finally {
    sampleLoading.value = false
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

function onFileChange(uploadFile: { raw?: File }) {
  sampleFile.value = uploadFile.raw ?? null
}

async function uploadSample() {
  if (!sampleFile.value) {
    ElMessage.warning('请先选择样本文件')
    return
  }
  if (!sampleForm.value.document_type.trim()) {
    ElMessage.warning('请填写文档类型')
    return
  }
  if (!sampleForm.value.category_path) {
    ElMessage.warning('请选择归档分类')
    return
  }
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', sampleFile.value)
    fd.append('document_type', sampleForm.value.document_type.trim())
    fd.append('category_path', sampleForm.value.category_path)
    await createSample(fd)
    ElMessage.success('样本已学习，后续同版式文档将自动归档')
    sampleForm.value.document_type = ''
    sampleFile.value = null
    await loadSamples()
  } finally {
    uploading.value = false
  }
}

async function toggleSample(s: DocumentSampleItem) {
  await updateSample(s.id, { enabled: s.enabled })
}

async function removeSample(s: DocumentSampleItem) {
  try {
    await ElMessageBox.confirm(`删除样本「${s.original_filename}」？`, '提示', { type: 'warning' })
  } catch {
    return
  }
  await deleteSample(s.id)
  ElMessage.success('已删除')
  await loadSamples()
}

function showFeatures(s: DocumentSampleItem) {
  featureData.value = s
  featureDialog.value = true
}

async function toggleTemplate(t: RecognitionTemplate) {
  await updateRecognitionTemplate(t.id, { enabled: !t.enabled })
  t.enabled = !t.enabled
  ElMessage.success(t.enabled ? '已启用，同类型文件将自动归档' : '已停用')
}

async function removeTemplate(t: RecognitionTemplate) {
  try {
    await ElMessageBox.confirm(`删除「${t.document_type}」模板？删除后该类型文件将重新走人工审核。`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  await deleteRecognitionTemplate(t.id)
  ElMessage.success('已删除')
  await loadTemplates()
}

onMounted(() => {
  loadSamples()
  loadTemplates()
  listCategories().then((tree) => {
    catPaths.value = collectPaths(tree)
  })
})
</script>

<template>
  <div class="mem-page">
    <el-card shadow="never" class="mb16">
      <template #header>
        <div class="head">
          <span>格式样本（主动学习 · 版式记忆）</span>
        </div>
      </template>
      <div class="gray small mb8">
        上传一份清晰、典型的文档作为样本，系统本地学习它的版式指纹（关键词 + 结构 + 字段锚点）。
        之后遇到<strong>名字完全模糊、但版式相同</strong>的文件，直接自动归档，纯本地、零 AI 消耗。
      </div>
      <div class="sample-upload">
        <el-upload
          :auto-upload="false"
          :limit="1"
          :show-file-list="true"
          :on-change="onFileChange"
          :on-remove="() => onFileChange({} as any)"
        >
          <el-button>选择样本文件</el-button>
        </el-upload>
        <el-input v-model="sampleForm.document_type" placeholder="文档类型，如：销售合同" style="width: 200px" />
        <el-select v-model="sampleForm.category_path" placeholder="归档分类" style="width: 220px">
          <el-option v-for="p in catPaths" :key="p" :label="p" :value="p" />
        </el-select>
        <el-button type="primary" :loading="uploading" @click="uploadSample">学习样本</el-button>
      </div>
      <el-table :data="samples" size="small" v-loading="sampleLoading" class="mt8">
        <el-table-column prop="document_type" label="文档类型" width="120" />
        <el-table-column prop="category_path" label="归档分类" width="170" />
        <el-table-column prop="original_filename" label="样本文件" min-width="180" show-overflow-tooltip />
        <el-table-column label="特征" width="190">
          <template #default="{ row }">
            <span class="gray small">{{ row.summary?.labels ?? 0 }}词 / {{ row.summary?.grams ?? 0 }}gram / {{ row.summary?.fields?.length ?? 0 }}字段</span>
          </template>
        </el-table-column>
        <el-table-column prop="usage_count" label="命中" width="70" />
        <el-table-column label="启用" width="70">
          <template #default="{ row }">
            <el-switch v-model="row.enabled" @change="toggleSample(row)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130">
          <template #default="{ row }">
            <el-button link type="primary" @click="showFeatures(row)">特征</el-button>
            <el-button link type="danger" @click="removeSample(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!sampleLoading && !samples.length" description="还没有格式样本，上传一份清晰文档开始学习" :image-size="60" />
    </el-card>

    <el-card shadow="never" class="mb16">
      <template #header>
        <div class="head">
          <span>已学习的识别模板（自动积累 · 字段记忆）</span>
        </div>
      </template>
      <div class="gray small mb8">
        人工审核确认归档后，系统会记住该类型文件的归档方式；下次再遇<strong>同类型且关键字段齐全</strong>的文件，自动归档、不进人工审核、不耗 AI。
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

    <!-- 样本特征对话框 -->
    <el-dialog v-model="featureDialog" title="样本特征" width="640px">
      <template v-if="featureData">
        <p class="m0"><b>{{ featureData.original_filename }}</b></p>
        <p class="gray small m0 mt4">类型：{{ featureData.document_type }}　归档：{{ featureData.category_path }}</p>
        <el-descriptions :column="2" size="small" border class="mt8">
          <el-descriptions-item label="字符数">{{ featureData.summary?.stats?.chars ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="行数">{{ featureData.summary?.stats?.lines ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="数字占比">{{ featureData.summary?.stats?.digit_ratio ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="命中次数">{{ featureData.usage_count }}</el-descriptions-item>
        </el-descriptions>
        <div class="mt8">
          <div class="gray small mb4">可提取字段（锚点）</div>
          <el-tag v-for="f in featureData.summary?.fields ?? []" :key="f" size="small" class="mr4 mb4">{{ f }}</el-tag>
          <div v-if="!(featureData.summary?.fields?.length)" class="gray small">无</div>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.mem-page {
  max-width: 960px;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.mb16 { margin-bottom: 16px; }
.mb8 { margin-bottom: 8px; }
.mt8 { margin-top: 8px; }
.mt4 { margin-top: 4px; }
.mb4 { margin-bottom: 4px; }
.m0 { margin: 0; }
.mr4 { margin-right: 4px; }
.gray { color: #909399; }
.small { font-size: 12px; }
.sample-upload {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
@media (max-width: 768px) {
  .sample-upload .el-input,
  .sample-upload .el-select {
    width: 100% !important;
  }
}
</style>
