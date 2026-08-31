// 移动端检测 composable：<768px 视为手机
import { onBeforeUnmount, onMounted, ref } from 'vue'

export function useMobile() {
  const isMobile = ref(false)

  function check() {
    isMobile.value = window.innerWidth < 768
  }

  onMounted(() => {
    check()
    window.addEventListener('resize', check)
  })
  onBeforeUnmount(() => window.removeEventListener('resize', check))

  return { isMobile }
}
