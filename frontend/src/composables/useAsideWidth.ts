/**
 * 可拖拽左侧栏宽度：拖动列表栏与详情区之间的分隔条自由调整宽度，
 * 宽度记忆到 localStorage，刷新后保持。
 * 用于业务档案 / 文档库等"左列表 + 右详情"布局。
 */
import { onBeforeUnmount, ref } from 'vue'

export function useAsideWidth(key: string, defaultWidth = 340, minW = 240, maxW = 640) {
  const asideWidth = ref(defaultWidth)
  try {
    const saved = localStorage.getItem(key)
    if (saved) {
      const n = parseInt(saved, 10)
      if (Number.isFinite(n) && n >= minW && n <= maxW) asideWidth.value = n
    }
  } catch {
    /* 忽略 localStorage 不可用 */
  }

  let startX = 0
  let startW = 0

  function onDrag(e: MouseEvent) {
    const delta = e.clientX - startX
    asideWidth.value = Math.min(maxW, Math.max(minW, startW + delta))
  }

  function endDrag() {
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    document.removeEventListener('mousemove', onDrag)
    document.removeEventListener('mouseup', endDrag)
    try {
      localStorage.setItem(key, String(Math.round(asideWidth.value)))
    } catch {
      /* 忽略 */
    }
  }

  function startResize(e: MouseEvent) {
    e.preventDefault()
    startX = e.clientX
    startW = asideWidth.value
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    document.addEventListener('mousemove', onDrag)
    document.addEventListener('mouseup', endDrag)
  }

  onBeforeUnmount(endDrag)

  return { asideWidth, startResize }
}
