import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'dashboard',
      component: () => import('@/views/DashboardView.vue'),
      meta: { title: '数据看板' },
    },
    {
      path: '/organize',
      name: 'organize',
      component: () => import('@/views/OrganizeView.vue'),
      meta: { title: '文件整理' },
    },
    {
      path: '/review',
      name: 'review',
      component: () => import('@/views/ReviewView.vue'),
      meta: { title: '待人工确认' },
    },
    {
      path: '/library',
      name: 'library',
      component: () => import('@/views/LibraryView.vue'),
      meta: { title: '文档库' },
    },
    {
      path: '/recycle-bin',
      name: 'recycle-bin',
      component: () => import('@/views/RecycleBinView.vue'),
      meta: { title: '回收站' },
    },
    {
      path: '/categories',
      name: 'categories',
      component: () => import('@/views/CategoryView.vue'),
      meta: { title: '分类管理' },
    },
    {
      path: '/memories',
      name: 'memories',
      component: () => import('@/views/MemoryView.vue'),
      meta: { title: '自动归档' },
    },
    {
      path: '/logs',
      name: 'logs',
      component: () => import('@/views/LogsView.vue'),
      meta: { title: '操作日志' },
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('@/views/SettingsView.vue'),
      meta: { title: '系统设置' },
    },
  ],
})

export default router
