<template>
  <div id="app-container">
    <!-- 顶部导航 -->
    <el-container v-if="showLayout">
      <el-aside width="220px" class="sidebar">
        <div class="logo">
          <img src="@/assets/logo.svg" alt="Logo" v-if="false" />
          <h2>🦞 海鲜视频工坊</h2>
        </div>
        <el-menu
          :default-active="activeMenu"
          router
          class="sidebar-menu"
        >
          <el-menu-item index="/dashboard">
            <el-icon><Odometer /></el-icon>
            <span>工作台</span>
          </el-menu-item>
          <el-menu-item index="/materials">
            <el-icon><Folder /></el-icon>
            <span>素材库</span>
          </el-menu-item>
          <el-menu-item index="/projects">
            <el-icon><VideoCamera /></el-icon>
            <span>混剪项目</span>
          </el-menu-item>
          <el-menu-item index="/dedup">
            <el-icon><Search /></el-icon>
            <span>查重中心</span>
          </el-menu-item>
          <el-menu-item index="/publish">
            <el-icon><Promotion /></el-icon>
            <span>一键发布</span>
          </el-menu-item>
          <el-menu-item index="/accounts">
            <el-icon><User /></el-icon>
            <span>账号管理</span>
          </el-menu-item>
        </el-menu>
      </el-aside>

      <el-container>
        <el-header class="app-header">
          <div class="header-left">
            <h3>{{ pageTitle }}</h3>
          </div>
          <div class="header-right">
            <el-tag type="success" effect="dark" v-if="systemHealth">● 系统运行中</el-tag>
            <el-tag type="danger" effect="dark" v-else>● 系统异常</el-tag>
          </div>
        </el-header>

        <el-main class="app-main">
          <router-view v-slot="{ Component }">
            <transition name="fade" mode="out-in">
              <component :is="Component" />
            </transition>
          </router-view>
        </el-main>
      </el-container>
    </el-container>

    <router-view v-else />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const systemHealth = ref(true)

const showLayout = computed(() => route.path !== '/login')
const activeMenu = computed(() => route.path)

const pageTitles = {
  '/dashboard': '工作台',
  '/materials': '素材库',
  '/projects': '混剪项目',
  '/dedup': '查重中心',
  '/publish': '一键发布',
  '/accounts': '账号管理',
}
const pageTitle = computed(() => pageTitles[route.path] || '工作台')

onMounted(() => {
  // 健康检查
  fetch('/api/health')
    .then(res => res.json())
    .then(data => {
      systemHealth.value = data.status === 'healthy'
    })
    .catch(() => {
      systemHealth.value = false
    })
})
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  background: #f0f2f5;
}

#app-container {
  height: 100vh;
}

.sidebar {
  background: #001529;
  height: 100vh;
  overflow: hidden;
}

.logo {
  padding: 20px 16px;
  color: #fff;
  text-align: center;
  border-bottom: 1px solid rgba(255,255,255,0.1);
}

.logo h2 {
  font-size: 18px;
  color: #1890ff;
}

.sidebar-menu {
  border-right: none;
  background: transparent !important;
}

.sidebar-menu .el-menu-item {
  color: rgba(255,255,255,0.65);
}

.sidebar-menu .el-menu-item:hover {
  color: #fff;
  background: rgba(255,255,255,0.05);
}

.sidebar-menu .el-menu-item.is-active {
  color: #fff;
  background: #1890ff;
}

.app-header {
  background: #fff;
  border-bottom: 1px solid #e8e8e8;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
}

.app-header h3 {
  font-size: 18px;
  font-weight: 600;
  color: #262626;
}

.app-main {
  padding: 24px;
  overflow-y: auto;
  height: calc(100vh - 60px);
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
