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
    const msg = error?.response?.data?.detail || error?.message || '请求失败'
    ElMessage.error(typeof msg === 'string' ? msg : JSON.stringify(msg))
    return Promise.reject(error)
  },
)

export default http
