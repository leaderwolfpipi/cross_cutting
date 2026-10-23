<template>
  <div class="materials-page">
    <div class="page-header">
      <h2>素材库</h2>
      <el-upload
        :show-file-list="false"
        :before-upload="handleUpload"
        accept="video/*"
        multiple
      >
        <el-button type="primary">
          <el-icon><Upload /></el-icon> 上传视频
        </el-button>
      </el-upload>
    </div>

    <div class="filter-bar">
      <el-input
        v-model="searchQuery"
        placeholder="搜索素材..."
        style="width: 240px;"
        clearable
      />
      <el-select v-model="statusFilter" placeholder="状态" clearable style="width: 120px; margin-left: 12px;">
        <el-option label="活跃" value="active" />
        <el-option label="黑名单" value="blacklisted" />
      </el-select>
      <el-button @click="loadMaterials" style="margin-left: 12px;">
        <el-icon><Refresh /></el-icon> 刷新
      </el-button>
    </div>

    <el-table :data="filteredMaterials" stripe v-loading="loading" style="margin-top: 16px;">
      <el-table-column prop="filename" label="文件名" min-width="200" />
      <el-table-column label="时长" width="100">
        <template #default="{ row }">{{ formatDuration(row.duration) }}</template>
      </el-table-column>
      <el-table-column label="分辨率" width="120">
        <template #default="{ row }">{{ row.width }}×{{ row.height }}</template>
      </el-table-column>
      <el-table-column label="大小" width="100">
        <template #default="{ row }">{{ formatSize(row.file_size) }}</template>
      </el-table-column>
      <el-table-column prop="source_type" label="来源" width="100" />
      <el-table-column label="镜头数" width="80">
        <template #default="{ row }">{{ row.shot_count || 0 }}</template>
      </el-table-column>
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.status === 'active' ? 'success' : 'danger'" size="small">
            {{ row.status === 'active' ? '正常' : '黑名单' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="detectShots(row)" :loading="row.detecting">
            检测镜头
          </el-button>
          <el-button size="small" type="danger" @click="deleteMaterial(row)">
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

const materials = ref([])
const loading = ref(false)
const searchQuery = ref('')
const statusFilter = ref('')

const filteredMaterials = computed(() => {
  return materials.value.filter(m => {
    const matchSearch = !searchQuery.value ||
      m.filename.toLowerCase().includes(searchQuery.value.toLowerCase())
    const matchStatus = !statusFilter.value || m.status === statusFilter.value
    return matchSearch && matchStatus
  })
})

const loadMaterials = async () => {
  loading.value = true
  try {
    const res = await fetch('/api/materials/')
    materials.value = await res.json()
  } catch (e) {
    ElMessage.error('加载素材失败')
  } finally {
    loading.value = false
  }
}

const handleUpload = async (file) => {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('source_type', 'self_shot')
  formData.append('description', '')

  try {
    const res = await fetch('/api/materials/upload', {
      method: 'POST',
      body: formData,
    })
    if (res.status === 409) {
      ElMessage.warning('素材已存在')
    } else if (res.ok) {
      ElMessage.success(`上传成功: ${file.name}`)
      loadMaterials()
    } else {
      const data = await res.json()
      ElMessage.error(data.detail || '上传失败')
    }
  } catch (e) {
    ElMessage.error('上传失败: ' + e.message)
  }
  return false // 阻止自动上传
}

const detectShots = async (row) => {
  row.detecting = true
  try {
    const res = await fetch(`/api/materials/${row.id}/detect-shots`, { method: 'POST' })
    const data = await res.json()
    ElMessage.success(`检测到 ${data.shots?.length || 0} 个镜头`)
    loadMaterials()
  } catch (e) {
    ElMessage.error('镜头检测失败')
  } finally {
    row.detecting = false
  }
}

const deleteMaterial = async (row) => {
  try {
    await ElMessageBox.confirm(`确定删除素材 "${row.filename}"？`, '确认', { type: 'warning' })
    await fetch(`/api/materials/${row.id}`, { method: 'DELETE' })
    ElMessage.success('已删除')
    loadMaterials()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败')
  }
}

const formatDuration = (sec) => {
  if (!sec) return '-'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

const formatSize = (bytes) => {
  if (!bytes) return '-'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + ' KB'
  return (bytes / 1024 / 1024).toFixed(1) + ' MB'
}

onMounted(loadMaterials)
</script>

<style scoped>
.materials-page h2 {
  margin-bottom: 20px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.filter-bar {
  display: flex;
  align-items: center;
  margin-bottom: 16px;
}
</style>
