import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    redirect: '/dashboard'
  },
  {
    path: '/dashboard',
    name: 'Dashboard',
    component: () => import('@/views/Dashboard.vue')
  },
  {
    path: '/materials',
    name: 'Materials',
    component: () => import('@/views/Materials.vue')
  },
  {
    path: '/projects',
    name: 'Projects',
    component: () => import('@/views/Projects.vue')
  },
  {
    path: '/projects/:id',
    name: 'ProjectDetail',
    component: () => import('@/views/ProjectDetail.vue')
  },
  {
    path: '/dedup',
    name: 'Dedup',
    component: () => import('@/views/Dedup.vue')
  },
  {
    path: '/publish',
    name: 'Publish',
    component: () => import('@/views/Publish.vue')
  },
  {
    path: '/accounts',
    name: 'Accounts',
    component: () => import('@/views/Accounts.vue')
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
