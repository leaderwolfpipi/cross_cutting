<template>
  <div class="project-detail" v-loading="loading">
    <div class="page-header">
      <el-button @click="$router.push('/projects')">
        <el-icon><ArrowLeft /></el-icon> 返回
      </el-button>
      <h2>{{ project?.name || '项目详情' }}</h2>
    </div>

    <el-tabs v-model="activeTab" type="card">
      <!-- 基本信息 -->
      <el-tab-pane label="基本信息" name="info">
        <el-descriptions :column="2" border v-if="project">
          <el-descriptions-item label="项目名称">{{ project.name }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="statusType(project.status)">{{ statusText(project.status) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="描述" :span="2">{{ project.description || '-' }}</el-descriptions-item>
          <el-descriptions-item label="创意种子">{{ project.creative_seed }}</el-descriptions-item>
          <el-descriptions-item label="口播脚本">{{ project.script || '-' }}</el-descriptions-item>
          <el-descriptions-item label="查重状态">
            <el-tag :type="project.dedup_passed ? 'success' : 'warning'">
              {{ project.dedup_passed ? '已通过' : '未检测' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="最高相似度">{{ (project.similarity_score * 100).toFixed(1) }}%</el-descriptions-item>
        </el-descriptions>

        <div class="action-bar" style="margin-top: 24px;">
          <el-button type="primary" @click="runDedup" :loading="dedupLoading">
            <el-icon><Search /></el-icon> 执行查重
          </el-button>
          <el-button type="success" @click="goPublish">
            <el-icon><Promotion /></el-icon> 去发布
          </el-button>
        </div>
      </el-tab-pane>

      <!-- 平台版本 -->
      <el-tab-pane label="平台版本" name="variants">
        <el-table :data="variants" stripe>
          <el-table-column prop="platform" label="平台" width="120">
            <template #default="{ row }">
              <el-tag>{{ platformName(row.platform) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="分辨率" width="120">
            <template #default="{ row }">{{ row.width }}×{{ row.height }}</template>
          </el-table-column>
          <el-table-column label="时长" width="100">
            <template #default="{ row }">{{ formatDuration(row.duration) }}</template>
          </el-table-column>
          <el-table-column label="大小" width="100">
            <template #default="{ row }">{{ formatSize(row.file_size) }}</template>
          </el-table-column>
          <el-table-column label="标题" min-width="180">
            <template #default="{ row }">
              <el-input v-model="row.title" size="small" placeholder="输入标题" />
            </template>
          </el-table-column>
          <el-table-column label="描述" min-width="180">
            <template #default="{ row }">
              <el-input v-model="row.description" size="small" type="textarea" :rows="1" placeholder="输入描述" />
            </template>
          </el-table-column>
          <el-table-column label="标签" min-width="150">
            <template #default="{ row }">
              <el-select v-model="row.tags" multiple size="small" placeholder="添加标签" style="width: 100%;">
                <el-option v-for="t in commonTags" :key="t" :label="t" :value="t" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="200" fixed="right">
            <template #default="{ row }">
              <el-button size="small" @click="saveVariant(row)">保存</el-button>
              <el-button size="small" type="success" @click="approveVariant(row)">通过</el-button>
              <el-button size="small" type="primary" @click="previewVideo(row)">预览</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 查重结果 -->
      <el-tab-pane label="查重结果" name="dedup">
        <div v-if="dedupResult">
          <el-alert
            :title="`决策: ${dedupResult.decision === 'pass' ? '✅ 通过' : dedupResult.decision === 'reject' ? '❌ 拒绝' : '⚠️ 需复核'}`"
            :type="dedupResult.decision === 'pass' ? 'success' : dedupResult.decision === 'reject' ? 'error' : 'warning'"
            show-icon
            :closable="false"
            style="margin-bottom: 24px;"
          />

          <el-row :gutter="16" style="margin-bottom: 24px;">
            <el-col :span="6">
              <el-statistic title="综合决策分" :value="dedupResult.decision_score" :precision="4" />
            </el-col>
            <el-col :span="6">
              <el-statistic title="片段覆盖率" :value="dedupResult.segment_coverage" :precision="4" />
            </el-col>
            <el-col :span="6">
              <el-statistic title="视觉相似度" :value="dedupResult.visual_score" :precision="4" />
            </el-col>
            <el-col :span="6">
              <el-statistic title="音频相似度" :value="dedupResult.audio_score" :precision="4" />
            </el-col>
          </el-row>

          <div v-if="dedupResult.similar_videos?.length">
            <h4>相似视频</h4>
            <el-table :data="dedupResult.similar_videos" stripe size="small">
              <el-table-column prop="variant_id" label="视频ID" width="280" />
              <el-table-column prop="decision_score" label="相似度" width="120" />
              <el-table-column prop="segment_coverage" label="片段覆盖" width="120" />
            </el-table>
          </div>
        </div>
        <el-empty v-else description="尚未执行查重检测" />
      </el-tab-pane>
    </el-tabs>

    <!-- 视频预览弹窗 -->
    <el-dialog v-model="previewVisible" title="视频预览" width="600px">
      <video v-if="previewUrl" :src="previewUrl" controls style="width: 100%;" />
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

const route = useRoute()
const router = useRouter()
const projectId = route.params.id

const project = ref(null)
const variants = ref([])
const activeTab = ref('info')
const loading = ref(false)
const dedupLoading = ref(false)
const dedupResult = ref(null)
const previewVisible = ref(false)
const previewUrl = ref('')

const commonTags = ['海鲜', '美食', '源头直发', '新鲜', '挑选技巧', '烹饪', '教程', '探店']

const loadProject = async () => {
  loading.value = true
  try {
    const res = await fetch(`/api/projects/${projectId}`)
    project.value = await res.json()
  } catch (e) {
    ElMessage.error('加载项目失败')
  } finally {
    loading.value = false
  }
}

const loadVariants = async () => {
  try {
    const res = await fetch(`/api/projects/${projectId}/variants`)
    variants.value = await res.json()
  } catch (e) {
    ElMessage.error('加载版本失败')
  }
}

const runDedup = async () => {
  dedupLoading.value = true
  try {
    const res = await fetch(`/api/projects/${projectId}/dedup`, { method: 'POST' })
    const data = await res.json()
    ElMessage.success(data.dedup_passed ? '✅ 查重通过！' : '⚠️ 存在重复风险')
    // 加载第一个variant的查重结果
    if (data.results?.length) {
      dedupResult.value = data.results[0]
      activeTab.value = 'dedup'
    }
    loadProject()
  } catch (e) {
    ElMessage.error('查重失败')
  } finally {
    dedupLoading.value = false
  }
}

const saveVariant = async (row) => {
  try {
    await fetch(`/api/projects/variants/${row.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: row.title,
        description: row.description,
        tags: row.tags,
      }),
    })
    ElMessage.success('已保存')
  } catch (e) {
    ElMessage.error('保存失败')
  }
}

const approveVariant = async (row) => {
  try {
    await fetch(`/api/projects/variants/${row.id}/approve`, { method: 'POST' })
    ElMessage.success('已通过审核')
    loadVariants()
  } catch (e) {
    ElMessage.error('操作失败')
  }
}

const previewVideo = (row) => {
  previewUrl.value = `/outputs/${row.file_path.split('/').pop()}`
  previewVisible.value = true
}

const goPublish = () => {
  router.push('/publish')
}

const platformName = (p) => {
  const map = { douyin: '抖音', kuaishou: '快手', video_channel: '视频号', xiaohongshu: '小红书' }
  return map[p] || p
}

const statusType = (s) => {
  const map = { draft: 'info', processing: 'warning', ready: 'success', reviewing: 'warning', published: 'success', failed: 'danger', approved: 'success', rejected: 'danger' }
  return map[s] || 'info'
}

const statusText = (s) => {
  const map = { draft: '草稿', processing: '处理中', ready: '待审核', reviewing: '审核中', published: '已发布', failed: '失败', approved: '已通过', rejected: '已拒绝' }
  return map[s] || s
}

const formatDuration = (sec) => {
  if (!sec) return '-'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

const formatSize = (bytes) => {
  if (!bytes) return '-'
  return (bytes / 1024 / 1024).toFixed(1) + ' MB'
}

onMounted(() => {
  loadProject()
  loadVariants()
})
</script>

<style scoped>
.project-detail h2 { margin: 0; }
.page-header { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }
.action-bar { display: flex; gap: 12px; }
</style>
