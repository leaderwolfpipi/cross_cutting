<template>
  <div class="projects-page">
    <div class="page-header">
      <h2>混剪项目</h2>
      <el-button type="primary" @click="showCreateDialog = true">
        <el-icon><Plus /></el-icon> 新建项目
      </el-button>
    </div>

    <div class="filter-bar">
      <el-input
        v-model="searchQuery"
        placeholder="搜索项目..."
        style="width: 240px;"
        clearable
      />
      <el-select v-model="statusFilter" placeholder="状态" clearable style="width: 120px; margin-left: 12px;">
        <el-option label="草稿" value="draft" />
        <el-option label="处理中" value="processing" />
        <el-option label="待审核" value="reviewing" />
        <el-option label="已完成" value="ready" />
        <el-option label="已发布" value="published" />
      </el-select>
    </div>

    <el-table :data="filteredProjects" stripe v-loading="loading" style="margin-top: 16px;">
      <el-table-column prop="name" label="项目名称" min-width="180" />
      <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />
      <el-table-column label="创意种子" width="100">
        <template #default="{ row }">{{ row.creative_seed }}</template>
      </el-table-column>
      <el-table-column label="查重" width="100">
        <template #default="{ row }">
          <el-tag :type="row.dedup_passed ? 'success' : 'warning'" size="small">
            {{ row.dedup_passed ? '已通过' : '未检测' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="相似度" width="100">
        <template #default="{ row }">
          <span :style="{ color: row.similarity_score > 0.7 ? '#f5222d' : '#52c41a' }">
            {{ (row.similarity_score * 100).toFixed(1) }}%
          </span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">
            {{ statusText(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="180">
        <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="240" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="$router.push(`/projects/${row.id}`)">查看</el-button>
          <el-button size="small" type="primary" @click="startEdit(row)" :disabled="row.status === 'processing'">
            开始混剪
          </el-button>
          <el-button size="small" type="danger" @click="deleteProject(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新建项目弹窗 -->
    <el-dialog v-model="showCreateDialog" title="新建混剪项目" width="600px">
      <el-form :model="newProject" label-width="100px">
        <el-form-item label="项目名称">
          <el-input v-model="newProject.name" placeholder="请输入项目名称" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="newProject.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="选择素材">
          <el-select v-model="newProject.selected_material_ids" multiple placeholder="选择素材" style="width: 100%;">
            <el-option v-for="m in materials" :key="m.id" :label="m.filename" :value="m.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="创意种子">
          <el-input-number v-model="newProject.creative_seed" :min="1" :max="99999" />
          <span style="margin-left: 8px; color: #8c8c8c;">不同种子产生不同剪辑效果</span>
        </el-form-item>
        <el-form-item label="口播脚本">
          <el-input v-model="newProject.script" type="textarea" :rows="3" placeholder="可选：填写口播/字幕脚本" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" @click="createProject" :loading="creating">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

const projects = ref([])
const materials = ref([])
const loading = ref(false)
const searchQuery = ref('')
const statusFilter = ref('')
const showCreateDialog = ref(false)
const creating = ref(false)

const newProject = ref({
  name: '',
  description: '',
  selected_material_ids: [],
  creative_seed: Math.floor(Math.random() * 99999) + 1,
  script: '',
})

const filteredProjects = computed(() => {
  return projects.value.filter(p => {
    const matchSearch = !searchQuery.value ||
      p.name.toLowerCase().includes(searchQuery.value.toLowerCase())
    const matchStatus = !statusFilter.value || p.status === statusFilter.value
    return matchSearch && matchStatus
  })
})

const loadProjects = async () => {
  loading.value = true
  try {
    const res = await fetch('/api/projects/')
    projects.value = await res.json()
  } catch (e) {
    ElMessage.error('加载项目失败')
  } finally {
    loading.value = false
  }
}

const loadMaterials = async () => {
  try {
    const res = await fetch('/api/materials/')
    materials.value = await res.json()
  } catch (e) {}
}

const createProject = async () => {
  if (!newProject.value.name) {
    ElMessage.warning('请输入项目名称')
    return
  }
  if (newProject.value.selected_material_ids.length === 0) {
    ElMessage.warning('请选择至少一个素材')
    return
  }

  creating.value = true
  try {
    const res = await fetch('/api/projects/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(newProject.value),
    })
    if (res.ok) {
      ElMessage.success('项目创建成功')
      showCreateDialog.value = false
      newProject.value = {
        name: '',
        description: '',
        selected_material_ids: [],
        creative_seed: Math.floor(Math.random() * 99999) + 1,
        script: '',
      }
      loadProjects()
    } else {
      ElMessage.error('创建失败')
    }
  } catch (e) {
    ElMessage.error('创建失败: ' + e.message)
  } finally {
    creating.value = false
  }
}

const startEdit = async (row) => {
  try {
    const res = await fetch(`/api/projects/${row.id}/edit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ platforms: ['douyin', 'kuaishou', 'video_channel', 'xiaohongshu'] }),
    })
    if (res.ok) {
      ElMessage.success('混剪任务已启动，请稍后在详情页查看结果')
      loadProjects()
    }
  } catch (e) {
    ElMessage.error('启动失败')
  }
}

const deleteProject = async (row) => {
  try {
    await ElMessageBox.confirm(`确定删除项目 "${row.name}"？`, '确认', { type: 'warning' })
    // TODO: 实现删除接口
    ElMessage.success('已删除')
    loadProjects()
  } catch (e) {}
}

const statusType = (status) => {
  const map = { draft: 'info', processing: 'warning', ready: 'success', reviewing: 'warning', published: 'success', failed: 'danger' }
  return map[status] || 'info'
}

const statusText = (status) => {
  const map = { draft: '草稿', processing: '处理中', ready: '待审核', reviewing: '审核中', published: '已发布', failed: '失败' }
  return map[status] || status
}

const formatDate = (date) => {
  if (!date) return '-'
  return new Date(date).toLocaleString('zh-CN')
}

onMounted(() => {
  loadProjects()
  loadMaterials()
})
</script>

<style scoped>
.projects-page h2 { margin-bottom: 20px; }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.filter-bar { display: flex; align-items: center; margin-bottom: 16px; }
</style>
