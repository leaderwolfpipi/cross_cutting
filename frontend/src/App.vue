<template>
  <router-view v-if="$route.meta.public" />
  <el-container v-else class="shell">
    <el-aside width="208px" class="aside">
      <div class="brand">🦀 海鲜视频工坊</div>
      <el-menu :default-active="active" router class="menu">
        <el-menu-item v-for="m in menu" :key="m.path" :index="m.path">
          <el-icon><component :is="m.icon" /></el-icon><span>{{ m.title }}</span>
          <el-badge v-if="m.badge && badges[m.badge]" :value="badges[m.badge]" class="badge" />
        </el-menu-item>
      </el-menu>
      <div class="foot">
        <div>渲染：{{ backendLabel }}</div>
        <el-link type="info" @click="logout">退出登录</el-link>
      </div>
    </el-aside>
    <el-container>
      <el-header class="header">
        <h3>{{ $route.meta.title }}</h3>
      </el-header>
      <el-main class="main"><router-view /></el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, auth } from '@/api'

const route = useRoute()
const router = useRouter()
const info = ref(null)
const badges = ref({})
const menu = [
  { path: '/dashboard', title: '工作台', icon: 'Odometer' },
  { path: '/materials', title: '素材库', icon: 'Film' },
  { path: '/projects', title: '混剪项目', icon: 'MagicStick' },
  { path: '/quotes', title: '报价系列', icon: 'Tickets' },
  { path: '/dedup', title: '查重中心', icon: 'DocumentChecked', badge: 'review' },
  { path: '/publish', title: '一键发布', icon: 'Promotion', badge: 'operator' },
  { path: '/accounts', title: '平台账号', icon: 'User', badge: 'auth' },
  { path: '/jobs', title: '任务监控', icon: 'Monitor' },
]
const active = computed(() => '/' + (route.path.split('/')[1] || 'dashboard'))
const backendLabel = computed(() => {
  const b = info.value?.render_backends
  if (!b) return '-'
  return b.jianying ? '剪映 me' : b.ffmpeg ? 'FFmpeg' : '不可用'
})

async function refreshBadges() {
  if (!auth.key || route.meta.public) return
  try {
    const d = await api.dashboard()
    badges.value = { review: d.variant_status?.review || 0, operator: d.awaiting_operator, auth: d.accounts_need_auth }
  } catch { /* shown by interceptor */ }
}
let timer
onMounted(async () => {
  if (auth.key && !route.meta.public) info.value = await api.info().catch(() => null)
  refreshBadges()
  timer = setInterval(refreshBadges, 15000)
})
onUnmounted(() => clearInterval(timer))
function logout() {
  auth.clear()
  router.push('/login')
}
</script>

<style>
:root { --bg: #f5f6f8; }
html, body, #app { margin: 0; height: 100%; background: var(--bg); font-family: -apple-system, 'PingFang SC', 'Noto Sans CJK SC', system-ui, sans-serif; }
.shell { height: 100vh; }
.aside { background: #0f2a43; color: #cfd8e3; display: flex; flex-direction: column; }
.brand { font-size: 18px; font-weight: 600; color: #fff; padding: 20px 18px; }
.menu { border-right: 0; background: transparent; flex: 1; }
.menu .el-menu-item { color: #cfd8e3; }
.menu .el-menu-item.is-active { color: #fff; background: #1d4a73; }
.menu .el-menu-item:hover { background: #17395a; }
.badge { margin-left: 8px; }
.foot { padding: 14px 18px; font-size: 12px; display: flex; justify-content: space-between; align-items: center; }
.header { display: flex; align-items: center; background: #fff; border-bottom: 1px solid #eee; }
.header h3 { margin: 0; font-weight: 600; }
.main { padding: 20px; }
.card-row { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 16px; }
.muted { color: #86909c; font-size: 12px; }
.toolbar { display: flex; gap: 8px; align-items: center; margin-bottom: 16px; flex-wrap: wrap; }
.spacer { flex: 1; }
</style>
