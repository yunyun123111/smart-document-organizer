<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createCategory,
  createRule,
  createTemplate,
  deleteCategory,
  deleteRule,
  deleteTemplate,
  listCategories,
  listRules,
  listTemplates,
  updateCategory,
  updateRule,
  type CategoryNode,
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

async function load() {
  loading.value = true
  try {
    tree.value = await listCategories()
  } finally {
    loading.value = false
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

const matchTypes = [
  { label: '包含', value: 'contains' },
  { label: '完全匹配', value: 'exact' },
  { label: '正则', value: 'regex' },
]

onMounted(load)
</script>

<template>
  <el-row :gutter="16">
    <el-col :span="8">
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

    <el-col :span="16">
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
</template>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.mb16 { margin-bottom: 16px; }
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
.mt8 { margin-top: 8px; }
</style>
