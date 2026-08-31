<script setup lang="ts">
// 应用根组件：侧边导航 + 主内容区
</script>

<template>
  <el-container class="layout">
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
