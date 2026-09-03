<script setup lang="ts">
/**
 * 通用文档预览组件（沉浸式预览）
 * - PDF：iframe 内嵌浏览器原生 PDF 查看器（自带翻页/缩放/打印，稳定可靠）
 * - 图片（jpg/png 等）：原图展示
 * - 其他格式：提示不支持在线预览，可打开原件
 * 统一走带鉴权的 downloadDocumentFile（axios 携带 token）下载为 Blob，
 * 再用 objectURL 展示，避免新标签页 401 / 文件接口直开被拦截。
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { downloadDocumentFile } from '@/api'

const props = defineProps<{
  docId: number
  name?: string
  fileType?: string
}>()

const loading = ref(true)
const error = ref('')
const objectUrl = ref('')

const ext = computed(() => (props.fileType || '').toLowerCase())
const isPdf = computed(() => ext.value === 'pdf')
const isImage = computed(() =>
  ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'tif', 'tiff'].includes(ext.value),
)

async function load() {
  loading.value = true
  error.value = ''
  if (objectUrl.value) {
    URL.revokeObjectURL(objectUrl.value)
    objectUrl.value = ''
  }
  try {
    const blob = await downloadDocumentFile(props.docId)
    objectUrl.value = URL.createObjectURL(blob)
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

function openOriginal() {
  window.open(`/api/documents/${props.docId}/file`, '_blank')
}

watch(() => props.docId, () => load())

onMounted(load)
onUnmounted(() => {
  if (objectUrl.value) URL.revokeObjectURL(objectUrl.value)
})
</script>

<template>
  <div class="doc-preview" v-loading="loading">
    <!-- 加载失败 -->
    <div v-if="!loading && error" class="pv-empty">
      <el-empty :description="error" :image-size="70" />
      <el-button size="small" @click="openOriginal">打开原件</el-button>
    </div>

    <!-- PDF：object 内嵌浏览器原生 PDF 查看器（避免 iframe 触发顶层导航） -->
    <object
      v-else-if="!loading && isPdf && objectUrl"
      :data="objectUrl"
      type="application/pdf"
      class="pv-frame"
      aria-label="文档预览"
    >
      当前环境不支持内嵌 PDF 预览，请使用「打开原件」查看
    </object>

    <!-- 图片 -->
    <div v-else-if="!loading && isImage && objectUrl" class="pv-img-wrap">
      <img :src="objectUrl" class="pv-img" alt="预览" />
    </div>

    <!-- 其他格式 -->
    <div v-else-if="!loading && !isPdf && !isImage" class="pv-empty">
      <el-empty description="该格式不支持在线预览，请使用「打开」查看原件" :image-size="70" />
      <el-button size="small" @click="openOriginal">打开原件</el-button>
    </div>
  </div>
</template>

<style scoped>
.doc-preview {
  height: 100%;
  overflow: hidden;
  background: #f5f6f8;
  display: flex;
  flex-direction: column;
}
.pv-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
}
.pv-frame {
  flex: 1;
  width: 100%;
  height: 100%;
  border: none;
  background: #fff;
}
.pv-img-wrap {
  flex: 1;
  overflow: auto;
  display: flex;
  justify-content: center;
  padding: 16px;
}
.pv-img {
  max-width: 100%;
  height: auto;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
}
</style>
