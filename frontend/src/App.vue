<script setup lang="ts">
// 应用根组件：侧边导航 + 主内容区
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getAuthStatus, login } from '@/api'

// 访问密码登录态
const locked = ref(false)
const password = ref('')
const logging = ref(false)

async function checkAuth() {
  try {
    const st = await getAuthStatus()
    locked.value = st.password_required && !localStorage.getItem('sdo_access_token')
  } catch {
    // 后端不可达时不锁（本机直连场景）
    locked.value = false
  }
}

async function doLogin() {
  if (!password.value) {
    ElMessage.warning('请输入访问密码')
    return
  }
  logging.value = true
  try {
    const res = await login(password.value)
    localStorage.setItem('sdo_access_token', res.token)
    password.value = ''
    locked.value = false
    ElMessage.success('登录成功')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '密码错误')
  } finally {
    logging.value = false
  }
}

onMounted(checkAuth)
</script>

<template>
  <div v-if="locked" class="login-mask">
    <div class="login-card">
      <h2>📁 智能文档整理</h2>
      <p class="login-tip">系统已开启访问密码保护，请输入密码</p>
      <el-input
        v-model="password"
        type="password"
        placeholder="访问密码"
        size="large"
        show-password
        @keyup.enter="doLogin"
      />
      <el-button type="primary" size="large" class="login-btn" :loading="logging" @click="doLogin">
        进入系统
      </el-button>
    </div>
  </div>
  <el-container v-else class="layout">
    <el-aside width="220px" class="aside">
      <div class="logo">
        <h2>📁 智能文档整理</h2>
      </div>
      <el-menu router :default-active="$route.path" class="menu">
        <el-menu-item index="/">
          <span>数据看板</span>
        </el-menu-item>
        <el-menu-item index="/organize">
          <span>文件整理</span>
        </el-menu-item>
        <el-menu-item index="/review">
          <span>待人工确认</span>
        </el-menu-item>
        <el-menu-item index="/library">
          <span>文档库</span>
        </el-menu-item>
        <el-menu-item index="/categories">
          <span>分类管理</span>
        </el-menu-item>
        <el-menu-item index="/logs">
          <span>操作日志</span>
        </el-menu-item>
        <el-menu-item index="/settings">
          <span>系统设置</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <span class="header-title">{{ $route.meta.title ?? '智能文档整理工具' }}</span>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.login-mask {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1e3a5f 0%, #0f2027 100%);
}
.login-card {
  width: 340px;
  background: #fff;
  border-radius: 12px;
  padding: 32px 28px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.25);
  text-align: center;
}
.login-card h2 {
  margin: 0 0 8px;
  color: #1e3a5f;
}
.login-tip {
  color: #909399;
  font-size: 13px;
  margin: 0 0 20px;
}
.login-btn {
  width: 100%;
  margin-top: 18px;
}
.layout {
  height: 100vh;
}
.aside {
  background-color: #001529;
  color: #fff;
}
.logo {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.logo h2 {
  color: #fff;
  font-size: 16px;
  margin: 0;
  white-space: nowrap;
}
.menu {
  border-right: none;
  background-color: transparent;
}
.menu :deep(.el-menu-item) {
  color: rgba(255, 255, 255, 0.75);
}
.menu :deep(.el-menu-item.is-active) {
  color: #fff;
  background-color: rgba(255, 255, 255, 0.12);
}
.header {
  background-color: #fff;
  border-bottom: 1px solid #e5e7eb;
  display: flex;
  align-items: center;
}
.header-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}
.main {
  background-color: #f5f7fa;
}
</style>
