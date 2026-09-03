<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createCategory,
  createRule,
  createSample,
  createTemplate,
  deleteCategory,
  deleteRule,
  deleteSample,
  deleteTemplate,
  listCategories,
  listRules,
  listSamples,
  listTemplates,
  updateCategory,
  updateRule,
  updateSample,
  type CategoryNode,
  type DocumentSampleItem,
  type RenameTemplate,
  type Rule,
} from '@/api'

const tree = ref<CategoryNode[]>([])
const loading = ref(false)
const selected = ref<CategoryNode | null>(null)
const rules = ref<Rule[]>([])
const templates = ref<RenameTemplate[]>([])

const catDialog = ref(false)
const catForm = ref({ name: '', parent_id: null as number | null, description: '' })

const ruleDialog = ref(false)
const ruleForm = ref({ keyword: '', match_type: 'contains', priority: 0, weight: 1 })

const tplDialog = ref(false)
const tplForm = ref({ template: '{日期}_{类型}_{公司}_{编号}' })

// ---------- 格式样本 ----------
const samples = ref<DocumentSampleItem[]>([])
const sampleLoading = ref(false)
const uploading = ref(false)
const sampleFile = ref<File | null>(null)
const sampleForm = ref({ document_type: '', category_path: '' })
const catPaths = ref<string[]>([])
const featureDialog = ref(false)
const featureData = ref<DocumentSampleItem | null>(null)

function collectPaths(nodes: CategoryNode[]): string[] {
  const out: string[] = []
  for (const n of nodes) {
    out.push(n.path)
    if (n.children?.length) out.push(...collectPaths(n.children))
  }
  return out
}

async function load() {
  loading.value = true
  try {
    tree.value = await listCategories()
    catPaths.value = collectPaths(tree.value)
  } finally {
    loading.value = false
  }
}

async function loadSamples() {
  sampleLoading.value = true
  try {
    samples.value = await listSamples()
  } finally {
    sampleLoading.value = false
  }
}

async function select(cat: CategoryNode) {
  selected.value = cat
  rules.value = await listRules(cat.id)
  templates.value = await listTemplates(cat.id)
}

async function saveCategory() {
  if (!catForm.value.name.trim()) return
  await createCategory(catForm.value)
  catDialog.value = false
  ElMessage.success('已创建')
  await load()
}

async function removeCategory(cat: CategoryNode) {
  try {
    await ElMessageBox.confirm(`删除分类「${cat.path}」？`, '提示', { type: 'warning' })
  } catch {
    return
  }
  await deleteCategory(cat.id)
  ElMessage.success('已删除')
  if (selected.value?.id === cat.id) selected.value = null
  await load()
}

async function toggleEnabled(cat: CategoryNode) {
  await updateCategory(cat.id, { enabled: cat.enabled })
}

async function saveRule() {
  if (!selected.value || !ruleForm.value.keyword.trim()) return
  await createRule({ ...ruleForm.value, category_id: selected.value.id })
  ruleDialog.value = false
  ElMessage.success('已添加规则')
  rules.value = await listRules(selected.value.id)
}

async function removeRule(r: Rule) {
  await deleteRule(r.id)
  rules.value = await listRules(selected.value!.id)
}

async function toggleRule(r: Rule) {
  await updateRule(r.id, { enabled: r.enabled })
}

async function saveTemplate() {
  if (!selected.value) return
  await createTemplate({ category_id: selected.value.id, template: tplForm.value.template })
  tplDialog.value = false
  ElMessage.success('已保存模板')
  templates.value = await listTemplates(selected.value.id)
}

async function removeTemplate(t: RenameTemplate) {
  await deleteTemplate(t.id)
  templates.value = await listTemplates(selected.value!.id)
}

function openCreate(parent: CategoryNode | null) {
  catForm.value = { name: '', parent_id: parent?.id ?? null, description: '' }
  catDialog.value = true
}

// ---------- 样本操作 ----------
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

const matchTypes = [
  { label: '包含', value: 'contains' },
  { label: '完全匹配', value: 'exact' },
  { label: '正则', value: 'regex' },
]

onMounted(() => {
  load()
  loadSamples()
})
</script>

<template>
  <el-row :gutter="16">
    <el-col :xs="24" :span="8">
      <el-card shadow="never">
        <template #header>
          <div class="head">
            <span>分类树</span>
            <el-button size="small" type="primary" @click="openCreate(null)">+ 新建</el-button>
          </div>
        </template>
        <el-tree
          :data="tree"
          node-key="id"
          :props="{ label: 'name', children: 'children' }"
          highlight-current
          v-loading="loading"
          class="cat-tree"
          @node-click="select"
        >
          <template #default="{ data }">
            <div class="tree-node">
              <span class="tree-label">{{ data.name }}</span>
              <span class="tree-actions">
                <el-icon class="del" @click.stop="removeCategory(data as any)"><delete /></el-icon>
              </span>
            </div>
          </template>
        </el-tree>
      </el-card>
    </el-col>

    <el-col :xs="24" :span="16">
      <template v-if="selected">
        <el-card shadow="never" class="mb16">
          <template #header>
            <div class="head">
              <span>{{ selected.path }}</span>
              <div>
                <el-button size="small" @click="openCreate(selected)">+ 子分类</el-button>
                <el-switch v-model="selected.enabled" active-text="启用" @change="toggleEnabled(selected)" />
              </div>
            </div>
          </template>
          <p class="desc">{{ selected.description || '暂无描述' }}</p>
        </el-card>

        <el-card shadow="never" class="mb16">
          <template #header>
            <div class="head">
              <span>识别规则（关键词）</span>
              <el-button size="small" type="primary" @click="ruleDialog = true">+ 添加规则</el-button>
            </div>
          </template>
          <el-table :data="rules" size="small">
            <el-table-column prop="keyword" label="关键词" min-width="160" />
            <el-table-column label="匹配" width="90">
              <template #default="{ row }">
                {{ matchTypes.find((m) => m.value === row.match_type)?.label ?? row.match_type }}
              </template>
            </el-table-column>
            <el-table-column prop="priority" label="优先级" width="80" />
            <el-table-column prop="weight" label="权重" width="80" />
            <el-table-column label="启用" width="70">
              <template #default="{ row }">
                <el-switch v-model="row.enabled" @change="toggleRule(row)" />
              </template>
            </el-table-column>
            <el-table-column label="操作" width="70">
              <template #default="{ row }">
                <el-button link type="danger" @click="removeRule(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card shadow="never">
          <template #header>
            <div class="head">
              <span>重命名模板</span>
              <el-button size="small" type="primary" @click="tplDialog = true">+ 添加模板</el-button>
            </div>
          </template>
          <el-table :data="templates" size="small">
            <el-table-column prop="template" label="模板" />
            <el-table-column label="启用" width="70">
              <template #default="{ row }">{{ row.enabled ? '是' : '否' }}</template>
            </el-table-column>
            <el-table-column label="操作" width="70">
              <template #default="{ row }">
                <el-button link type="danger" @click="removeTemplate(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <div class="gray small mt8">
            变量：{日期} {类型} {公司} {编号} {订单号} {发票号} {金额} …（英文别名同义）
          </div>
        </el-card>
      </template>
      <el-empty v-else description="选择左侧分类查看与配置" />
    </el-col>
  </el-row>

  <!-- 格式样本（自动学习） -->
  <el-card shadow="never" class="mb16 sample-card">
    <template #header>
      <div class="head">
        <span>格式样本（自动学习）</span>
        <span class="gray small">上传清晰样本 → 学习版式指纹 → 后续同版式文档自动归档（纯本地、零 AI 消耗）</span>
      </div>
    </template>
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

  <!-- 分类对话框 -->
  <el-dialog v-model="catDialog" title="新建分类" width="420px">
    <el-form label-width="80px">
      <el-form-item label="名称">
        <el-input v-model="catForm.name" placeholder="如：销售合同" />
      </el-form-item>
      <el-form-item label="描述">
        <el-input v-model="catForm.description" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="catDialog = false">取消</el-button>
      <el-button type="primary" @click="saveCategory">保存</el-button>
    </template>
  </el-dialog>

  <!-- 规则对话框 -->
  <el-dialog v-model="ruleDialog" title="添加规则" width="420px">
    <el-form label-width="80px">
      <el-form-item label="关键词">
        <el-input v-model="ruleForm.keyword" placeholder="命中即加分，如：增值税" />
      </el-form-item>
      <el-form-item label="匹配方式">
        <el-select v-model="ruleForm.match_type">
          <el-option v-for="m in matchTypes" :key="m.value" :label="m.label" :value="m.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="优先级">
        <el-input-number v-model="ruleForm.priority" :min="0" />
      </el-form-item>
      <el-form-item label="权重">
        <el-input-number v-model="ruleForm.weight" :min="0.1" :max="5" :step="0.1" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="ruleDialog = false">取消</el-button>
      <el-button type="primary" @click="saveRule">保存</el-button>
    </template>
  </el-dialog>

  <!-- 模板对话框 -->
  <el-dialog v-model="tplDialog" title="添加重命名模板" width="440px">
    <el-input v-model="tplForm.template" />
    <div class="gray small mt8">示例：{日期}_{类型}_{公司}_{编号}</div>
    <template #footer>
      <el-button @click="tplDialog = false">取消</el-button>
      <el-button type="primary" @click="saveTemplate">保存</el-button>
    </template>
  </el-dialog>

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
</template>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.mb16 { margin-bottom: 16px; }
.mt8 { margin-top: 8px; }
.mt4 { margin-top: 4px; }
.mb4 { margin-bottom: 4px; }
.m0 { margin: 0; }
.mr4 { margin-right: 4px; }
.desc { color: #909399; margin: 0; font-size: 13px; }
.tree-node {
  flex: 1;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.tree-label { flex: 1; }
.del { color: #f56c6c; }
.gray { color: #909399; }
.small { font-size: 12px; }
.sample-card { margin-top: 16px; }
.sample-upload {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}
@media (max-width: 768px) {
  .cat-tree {
    max-height: 35vh;
  }
  .head .el-button {
    padding-left: 10px;
    padding-right: 10px;
  }
  .sample-upload .el-input,
  .sample-upload .el-select {
    width: 100% !important;
  }
}
</style>
