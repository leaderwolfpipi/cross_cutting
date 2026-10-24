import { createRouter, createWebHistory } from 'vue-router'
import { auth } from '@/api'

const routes = [
  { path: '/', redirect: '/dashboard' },
  { path: '/login', component: () => import('@/views/Login.vue'), meta: { public: true, title: '登录' } },
  { path: '/dashboard', component: () => import('@/views/Dashboard.vue'), meta: { title: '工作台' } },
  { path: '/materials', component: () => import('@/views/Materials.vue'), meta: { title: '素材库' } },
  { path: '/projects', component: () => import('@/views/Projects.vue'), meta: { title: '混剪项目' } },
  { path: '/projects/:id', component: () => import('@/views/ProjectDetail.vue'), meta: { title: '项目详情' } },
  { path: '/quotes', component: () => import('@/views/Quotes.vue'), meta: { title: '全规格报价系列' } },
  { path: '/dedup', component: () => import('@/views/Dedup.vue'), meta: { title: '查重中心' } },
  { path: '/publish', component: () => import('@/views/Publish.vue'), meta: { title: '一键发布' } },
  { path: '/accounts', component: () => import('@/views/Accounts.vue'), meta: { title: '平台账号' } },
  { path: '/jobs', component: () => import('@/views/Jobs.vue'), meta: { title: '任务监控' } },
]

const router = createRouter({ history: createWebHistory(), routes })
router.beforeEach((to) => {
  if (!to.meta.public && !auth.key) return '/login'
})
export default router
