/**
 * 可拖拽左右分屏：拖动中间分隔条自由调整左/右区域宽度。
 * 用于"待人工确认"与"文档库"的左预览 + 右信息核对分屏。
 */
import { onBeforeUnmount, ref } from 'vue'

export function useSplitDrag(initialRatio = 55, minRatio = 20, maxRatio = 80) {
  const leftRatio = ref(initialRatio)
  let startX = 0
  let startRatio = 0

  function onDrag(e: MouseEvent) {
    const splitEl = document.querySelector('.review-split') as HTMLElement | null
    if (!splitEl) return
    const rect = splitEl.getBoundingClientRect()
    if (rect.width <= 0) return
    const delta = ((e.clientX - startX) / rect.width) * 100
    leftRatio.value = Math.min(maxRatio, Math.max(minRatio, startRatio + delta))
  }

  function endDrag() {
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    document.removeEventListener('mousemove', onDrag)
    document.removeEventListener('mouseup', endDrag)
  }

  function startDrag(e: MouseEvent) {
    startX = e.clientX
    startRatio = leftRatio.value
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    document.addEventListener('mousemove', onDrag)
    document.addEventListener('mouseup', endDrag)
  }

  onBeforeUnmount(endDrag)

  return { leftRatio, startDrag }
}
