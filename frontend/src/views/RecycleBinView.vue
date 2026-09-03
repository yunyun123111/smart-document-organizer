<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listRecycleBin,
  restoreRecycleItem,
  batchRestoreRecycle,
  permanentDeleteRecycleItem,
  batchPermanentDeleteRecycle,
  emptyRecycleBin,
  type RecycleBinItem,
} from '@/api'

const items = ref<RecycleBinItem[]>([])
const loading = ref(false)
const selectedIds = ref<number[]>([])
const busy = ref(false)

const totalSize = computed(() => {
  const bytes = items.value.reduce((s, i) => s + (i.file_size || 0), 0)
  return formatSize(bytes)
})

function formatSize(n: number): string {
  if (!n) return '0 B'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}

function formatTime(t?: string): string {
  return t ? t.replace('T', ' ').slice(0, 19) : ''
}

const statusLabel = (s: string) => {
  const map: Record<string, string> = {
    archived: '已归档',
    need_review: '待确认',
    processed: '已识别',
    pending: '待整理',
    failed: '识别失败',
    duplicate: '重复文件',
    skipped: '已跳过',
    recycled: '回收站',
  }
  return map[s] ?? s
}

async function load() {
  loading.value = true
  try {
    const res = await listRecycleBin()
    items.value = res.items
  } catch {
    /* 错误已在拦截器提示 */
  } finally {
    loading.value = false
  }
}

function onSelectionChange(rows: RecycleBinItem[]) {
  selectedIds.value = rows.map((r) => r.id)
}

async function restoreOne(row: RecycleBinItem) {
  try {
    await restoreRecycleItem(row.id)
    ElMessage.success(row.original_file_missing ? '已恢复记录' : '已恢复')
    await load()
  } catch {
    /* 错误已在拦截器提示 */
  }
}

async function permanentDeleteOne(row: RecycleBinItem) {
  try {
    await ElMessageBox.confirm(
      `⚠ 永久删除\n此操作将永久删除文件「${row.original_filename}」，删除后无法恢复。确定继续吗？`,
      '永久删除',
      { type: 'warning', confirmButtonText: '永久删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await permanentDeleteRecycleItem(row.id)
    ElMessage.success('已永久删除')
    await load()
  } catch {
    /* 错误已在拦截器提示 */
  }
}

async function batchRestore() {
  if (!selectedIds.value.length) {
    ElMessage.warning('请先勾选要恢复的文件')
    return
  }
  busy.value = true
  try {
    const res = await batchRestoreRecycle(selectedIds.value)
    if (res.failed_count) {
      ElMessage.warning(`成功恢复 ${res.restored_count} 个，失败 ${res.failed_count} 个`)
    } else {
      ElMessage.success(`已恢复 ${res.restored_count} 个文件`)
    }
    await load()
  } catch {
    /* 错误已在拦截器提示 */
  } finally {
    busy.value = false
  }
}

async function batchDelete() {
  if (!selectedIds.value.length) {
    ElMessage.warning('请先勾选要永久删除的文件')
    return
  }
  try {
    await ElMessageBox.confirm(
      `⚠ 永久删除\n已选择 ${selectedIds.value.length} 个文件，将被永久删除，删除后无法恢复。确定继续吗？`,
      '批量永久删除',
      { type: 'warning', confirmButtonText: '永久删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  busy.value = true
  try {
    const res = await batchPermanentDeleteRecycle(selectedIds.value)
    if (res.failed_count) {
      ElMessage.warning(`成功永久删除 ${res.deleted_count} 个，失败 ${res.failed_count} 个`)
    } else {
      ElMessage.success(`已永久删除 ${res.deleted_count} 个文件`)
    }
    await load()
  } catch {
    /* 错误已在拦截器提示 */
  } finally {
    busy.value = false
  }
}

async function empty() {
  try {
    await ElMessageBox.confirm(
      `⚠ 清空回收站\n回收站中共 ${items.value.length} 个文件（约 ${totalSize.value}）。\n清空后文件将无法恢复！确定继续吗？`,
      '清空回收站',
      { type: 'error', confirmButtonText: '清空', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  busy.value = true
  try {
    const res = await emptyRecycleBin()
    if (res.failed) {
      ElMessage.warning(`清空完成：成功 ${res.ok_count} 个，失败 ${res.failed} 个`)
    } else {
      ElMessage.success('回收站已清空')
    }
    await load()
  } catch {
    /* 错误已在拦截器提示 */
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <el-card shadow="never">
    <template #header>
      <div class="head">
        <span>回收站（{{ items.length }} 项，约 {{ totalSize }}）</span>
        <div class="head-actions">
          <el-button size="small" @click="load">刷新</el-button>
          <el-button
            size="small"
            type="success"
            plain
            :disabled="!selectedIds.length"
            :loading="busy"
            @click="batchRestore"
          >
            批量恢复（{{ selectedIds.length }}）
          </el-button>
          <el-button
            size="small"
            type="danger"
            plain
            :disabled="!selectedIds.length"
            :loading="busy"
            @click="batchDelete"
          >
            批量永久删除（{{ selectedIds.length }}）
          </el-button>
          <el-button size="small" type="danger" :disabled="!items.length" :loading="busy" @click="empty">
            清空回收站
          </el-button>
        </div>
      </div>
    </template>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="删除的文件不会立即永久删除，而是进入回收站；你可以随时恢复，或手动永久删除。"
      class="tip"
    />

    <el-table
      :data="items"
      v-loading="loading"
      style="width: 100%"
      @selection-change="onSelectionChange"
      empty-text="回收站是空的"
    >
      <el-table-column type="selection" width="42" />
      <el-table-column label="文件名" min-width="240" show-overflow-tooltip>
        <template #default="{ row }">
          <span>{{ row.current_filename || row.original_filename }}</span>
          <el-tag v-if="row.original_file_missing || !row.file_exists" size="small" type="warning" class="miss-tag">
            原文件缺失
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="类型" width="130" show-overflow-tooltip>
        <template #default="{ row }">{{ row.document_type || '未识别' }}</template>
      </el-table-column>
      <el-table-column label="原分类" width="140" show-overflow-tooltip>
        <template #default="{ row }">{{ row.category_path || '-' }}</template>
      </el-table-column>
      <el-table-column label="原状态" width="90">
        <template #default="{ row }">
          <el-tag size="small">{{ statusLabel(row.original_status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="大小" width="90">
        <template #default="{ row }">{{ formatSize(row.file_size) }}</template>
      </el-table-column>
      <el-table-column label="删除原因" width="130" show-overflow-tooltip>
        <template #default="{ row }">{{ row.deleted_reason || '-' }}</template>
      </el-table-column>
      <el-table-column label="删除时间" width="165">
        <template #default="{ row }">{{ formatTime(row.deleted_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" :disabled="!row.file_exists" @click="restoreOne(row)">
            恢复
          </el-button>
          <el-button link type="danger" @click="permanentDeleteOne(row)">永久删除</el-button>
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
  flex-wrap: wrap;
  gap: 8px;
}
.head-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.tip {
  margin-bottom: 12px;
}
.miss-tag {
  margin-left: 6px;
}
</style>
