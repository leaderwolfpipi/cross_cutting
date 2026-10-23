<template>
  <div class="accounts-page">
    <div class="page-header">
      <h2>账号管理</h2>
      <el-button type="primary" @click="showAddDialog = true">
        <el-icon><Plus /></el-icon> 添加账号
      </el-button>
    </div>

    <el-table :data="accounts" stripe v-loading="loading">
      <el-table-column prop="platform" label="平台" width="120">
        <template #default="{ row }">
          <el-tag>{{ platformName(row.platform) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="account_name" label="账号名称" min-width="150" />
      <el-table-column prop="account_id" label="账号ID" width="200" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="row.status === 'active' ? 'success' : 'danger'" size="small">
            {{ row.status === 'active' ? '正常' : '异常' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="Token过期" width="180">
        <template #default="{ row }">{{ formatDate(row.token_expires_at) || '未设置' }}</template>
      </el-table-column>
      <el-table-column label="创建时间" width="180">
        <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="160" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="reauth(row)">重新授权</el-button>
          <el-button size="small" type="danger" @click="deleteAccount(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="showAddDialog" title="添加平台账号" width="500px">
      <el-form :model="newAccount" label-width="100px">
        <el-form-item label="平台">
          <el-select v-model="newAccount.platform" placeholder="选择平台">
            <el-option label="抖音" value="douyin" />
            <el-option label="快手" value="kuaishou" />
            <el-option label="视频号" value="video_channel" />
            <el-option label="小红书" value="xiaohongshu" />
          </el-select>
        </el-form-item>
        <el-form-item label="账号名称">
          <el-input v-model="newAccount.account_name" placeholder="账号昵称" />
        </el-form-item>
        <el-form-item label="账号ID">
          <el-input v-model="newAccount.account_id" placeholder="平台账号ID" />
        </el-form-item>
        <el-form-item label="Access Token">
          <el-input v-model="newAccount.access_token" placeholder="OAuth access_token" />
        </el-form-item>
        <el-form-item label="Refresh Token">
          <el-input v-model="newAccount.refresh_token" placeholder="OAuth refresh_token" />
        </el-form-item>
        <el-form-item label="Open ID">
          <el-input v-model="newAccount.open_id" placeholder="用户OpenID" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAddDialog = false">取消</el-button>
        <el-button type="primary" @click="addAccount" :loading="adding">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

const accounts = ref([])
const loading = ref(false)
const showAddDialog = ref(false)
const adding = ref(false)

const newAccount = ref({
  platform: '',
  account_name: '',
  account_id: '',
  access_token: '',
  refresh_token: '',
  open_id: '',
})

const loadAccounts = async () => {
  loading.value = true
  try {
    const res = await fetch('/api/publish/accounts')
    accounts.value = await res.json()
  } catch (e) {
    ElMessage.error('加载失败')
  } finally {
    loading.value = false
  }
}

const addAccount = async () => {
  if (!newAccount.value.platform) {
    ElMessage.warning('请选择平台')
    return
  }
  adding.value = true
  try {
    const res = await fetch('/api/publish/accounts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(newAccount.value),
    })
    if (res.ok) {
      ElMessage.success('账号已添加')
      showAddDialog.value = false
      newAccount.value = { platform: '', account_name: '', account_id: '', access_token: '', refresh_token: '', open_id: '' }
      loadAccounts()
    } else {
      ElMessage.error('添加失败')
    }
  } catch (e) {
    ElMessage.error('添加失败: ' + e.message)
  } finally {
    adding.value = false
  }
}

const reauth = (row) => {
  // 打开平台授权URL
  const authUrls = {
    douyin: 'https://open.douyin.com/platform/oauth/connect/',
    kuaishou: 'https://open.kuaishou.com/oauth2/authorize',
  }
  const url = authUrls[row.platform]
  if (url) {
    window.open(url, '_blank')
  } else {
    ElMessage.info('该平台暂不支持OAuth授权')
  }
}

const deleteAccount = async (row) => {
  try {
    await ElMessageBox.confirm(`确定删除账号 "${row.account_name}"？`, '确认', { type: 'warning' })
    await fetch(`/api/publish/accounts/${row.id}`, { method: 'DELETE' })
    ElMessage.success('已删除')
    loadAccounts()
  } catch (e) {}
}

const platformName = (p) => {
  const map = { douyin: '抖音', kuaishou: '快手', video_channel: '视频号', xiaohongshu: '小红书' }
  return map[p] || p
}

const formatDate = (date) => {
  if (!date) return null
  return new Date(date).toLocaleString('zh-CN')
}

onMounted(loadAccounts)
</script>

<style scoped>
.accounts-page h2 { margin-bottom: 20px; }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
</style>
