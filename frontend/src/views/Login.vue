<template>
  <div class="login">
    <el-card class="box">
      <h2>🦀 海鲜视频工坊</h2>
      <p class="muted">创意混剪 · 查重 · 一键多平台发布</p>
      <el-form @submit.prevent="submit">
        <el-form-item>
          <el-input v-model="key" type="password" show-password placeholder="API Key（后端 API_KEYS 配置）" size="large" />
        </el-form-item>
        <el-button type="primary" size="large" style="width: 100%" :loading="loading" native-type="submit">进入</el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, auth } from '@/api'

const key = ref('')
const loading = ref(false)
const router = useRouter()

async function submit() {
  if (!key.value) return
  loading.value = true
  auth.set(key.value)
  try {
    await api.info()
    router.push('/dashboard')
  } catch {
    auth.clear()
    ElMessage.error('API Key 无效')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login { height: 100vh; display: flex; align-items: center; justify-content: center; background: linear-gradient(135deg, #0f2a43, #1d6fa5); }
.box { width: 380px; padding: 8px 12px; }
h2 { margin: 0 0 4px; }
</style>
