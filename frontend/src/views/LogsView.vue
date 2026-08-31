<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { listLogs, undoLog, type LogItem } from '@/api'

const items = ref<LogItem[]>([])
const loading = ref(false)

const typeLabel = (t: string) => {
  const map: Record<string, string> = {
    IMPORT: '导入',
    PARSE: '解析',
    OCR: 'OCR',
    CLASSIFY: '识别',
    RENAME: '重命名',
    MOVE: '移动',
    ARCHIVE: '归档',
    USER_EDIT: '人工修改',
    UNDO: '撤销',
  }
  return map[t.toUpperCase()] ?? t
}

async function load() {
  loading.value = true
  try {
    items.value = await listLogs({ limit: 200 })
  } finally {
    loading.value = false
  }
}

async function undo(row: LogItem) {
  if (row.operation_type === 'UNDO') {
    ElMessage.info('撤销操作本身不可再撤销')
    return
  }
  if (row.operation_type !== 'ARCHIVE' && row.operation_type !== 'RENAME' && row.operation_type !== 'MOVE') {
    ElMessage.info('该操作类型不支持撤销')
    return
  }
  try {
    const res = await undoLog(row.id)
    ElMessage.success(`已撤销，恢复到：${res.restored_path ?? ''}`)
    await load()
  } catch {
    /* 错误已在拦截器提示 */
  }
}

onMounted(load)
</script>

<template>
  <el-card shadow="never">
    <template #header>
      <div class="head">
        <span>操作日志</span>
        <el-button size="small" @click="load">刷新</el-button>
      </div>
    </template>

    <el-table :data="items" v-loading="loading" style="width: 100%">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column label="操作" width="110">
        <template #default="{ row }">
          <el-tag size="small">{{ typeLabel(row.operation_type) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="来源" width="200" show-overflow-tooltip>
        <template #default="{ row }">{{ row.old_filename }}</template>
      </el-table-column>
      <el-table-column label="去向" min-width="240" show-overflow-tooltip>
        <template #default="{ row }">{{ row.new_filename }}</template>
      </el-table-column>
      <el-table-column label="结果" width="80">
        <template #default="{ row }">
          <el-tag :type="row.result === 'ok' ? 'success' : 'danger'" size="small">
            {{ row.result === 'ok' ? '成功' : '失败' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="时间" width="170" />
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="undo(row)">撤销</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
