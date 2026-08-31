import axios from 'axios'
import { ElMessage } from 'element-plus'

// 统一 axios 实例：前端 /api 通过 vite proxy 转发到后端
const http = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

// 访问密码令牌：登录后存 localStorage，所有请求自动携带
http.interceptors.request.use((config) => {
  const token = localStorage.getItem('sdo_access_token')
  if (token) {
    config.headers = config.headers || {}
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const url: string = error?.config?.url || ''
    const isLogin = url.includes('/auth/login')
    if (error?.response?.status === 401) {
      // 登录接口本身 401(密码错误) 不刷新页面；其余接口 401 表示令牌无效
      // 清掉本地令牌并刷新，让 App 登录掩码重新出现（修复"页面能进但接口全401"死锁）
      localStorage.removeItem('sdo_access_token')
      if (!isLogin) {
        window.location.reload()
      }
    }
    if (!isLogin) {
      const msg = error?.response?.data?.detail || error?.message || '请求失败'
      ElMessage.error(typeof msg === 'string' ? msg : JSON.stringify(msg))
    }
    return Promise.reject(error)
  },
)

export default http
