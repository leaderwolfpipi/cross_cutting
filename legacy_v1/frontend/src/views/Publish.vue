<template>
  <div class="publish-page">
    <h2>一键发布</h2>

    <el-alert
      title="发布说明"
      type="warning"
      :closable="false"
      description="抖音需要OAuth授权+人工确认；快手支持完整API自动发布；视频号和小红书通过半自动方式发布（系统生成适配文件和发布指引）。"
      style="margin-bottom: 24px;"
    />

    <!-- 选择项目 -->
    <el-card class="section-card" shadow="hover">
      <template #header>
        <span>选择项目</span>
      </template>
      <el-select v-model="selectedProjectId" placeholder="选择要发布的项目" style="width: 100%;" @change="loadVariants">
        <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
      </el-select>
    </el-card>

    <!-- 平台配置 -->
    <el-card class="section-card" shadow="hover" v-if="variants.length">
      <template #header>
        <span>平台发布配置</span>
      </template>

      <el-table :data="publishConfigs" stripe>
        <el-table-column label="平台" width="120">
          <template #default="{ row }">
            <el-tag>{{ platformName(row.platform) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="发布方式" width="120">
          <template #default="{ row }">
            <el-tag :type="row.auto ? 'success' : 'warning'" size="small">
              {{ row.auto ? '自动' : '半自动' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="标题" min-width="200">
          <template #default="{ row }">
            <el-input v-model="row.title" size="small" placeholder="输入发布标题" />
          </template>
        </el-table-column>
        <el-table-column label="描述" min-width="200">
          <template #default="{ row }">
            <el-input v-model="row.description" size="small" type="textarea" :rows="1" placeholder="输入描述" />
          </template>
        </el-table-column>
        <el-table-column label="话题标签" min-width="200">
          <template #default="{ row }">
            <el-select v-model="row.tags" multiple size="small" placeholder="选择标签" style="width: 100%;">
              <el-option v-for="t in commonTags" :key="t" :label="t" :value="t" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="定时发布" width="200">
          <template #default="{ row }">
            <el-date-picker v-model="row.scheduled_at" type="datetime" size="small" placeholder="可选" style="width: 100%;" />
          </template>
        </el-table-column>
        <el-table-column label="启用" width="60">
          <template #default="{ row }">
            <el-switch v-model="row.enabled" />
          </template>
        </el-table-column>
      </el-table>

      <div class="publish-action" style="margin-top: 24px; text-align: center;">
        <el-button type="danger" size="large" @click="startPublish" :loading="publishing">
          <el-icon><Promotion /></el-icon> 一键发布
        </el-button>
      </div>
    </el-card>

    <!-- 发布结果 -->
    <el-card class="section-card" shadow="hover" v-if="publishResult">
      <template #header>
        <span>发布结果</span>
      </template>

      <el-alert
        :title="publishResult.message"
        :type="publishResult.status === 'completed' ? 'success' : 'warning'"
        show-icon
        :closable="false"
        style="margin-bottom: 16px;"
      />

      <el-table :data="publishResult.results" stripe size="small">
        <el-table-column prop="platform" label="平台" width="100" />
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.success ? 'success' : 'danger'" size="small">
              {{ row.success ? '✓' : '✗' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="message" label="信息" min-width="200" />
        <el-table-column label="操作" width="200">
          <template #default="{ row }">
            <div v-if="row.requires_auth">
              <el-button size="small" type="primary">去授权</el-button>
            </div>
            <div v-else-if="row.requires_manual">
              <el-button size="small" type="warning" @click="showManualGuide(row)">查看指引</el-button>
            </div>
            <div v-else-if="row.share_url">
              <el-link :href="row.share_url" target="_blank" type="primary">查看作品</el-link>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 发布历史 -->
    <el-card class="section-card" shadow="hover">
      <template #header>
        <span>发布历史</span>
      </template>
      <el-table :data="publishHistory" stripe size="small" v-loading="historyLoading">
        <el-table-column prop="id" label="任务ID" width="280" show-overflow-tooltip />
        <el-table-column label="进度" width="200">
          <template #default="{ row }">
            <el-progress :percentage="row.progress" />
          </template>
        </el-table-column>
        <el-table-column label="结果" width="200">
          <template #default="{ row }">
            {{ row.completed_platforms }}/{{ row.total_platforms }} 平台成功
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'completed' ? 'success' : 'warning'" size="small">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="180">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 半自动发布指引弹窗 -->
    <el-dialog v-model="manualGuideVisible" title="半自动发布指引" width="500px">
      <div v-if="manualGuide">
        <el-alert :title="manualGuide.platform" type="info" :closable="false" style="margin-bottom: 16px;" />
        <ol>
          <li v-for="(step, i) in manualGuide.instructions" :key="i" style="margin-bottom: 8px;">{{ step }}</li>
        </ol>
        <el-divider />
        <p><strong>视频文件：</strong> {{ manualGuide.video_path }}</p>
        <p><strong>封面文件：</strong> {{ manualGuide.cover_path }}</p>
        <p><strong>标题：</strong> {{ manualGuide.title }}</p>
        <p><strong>描述：</strong> {{ manualGuide.description }}</p>
        <p><strong>标签：</strong> {{ manualGuide.tags?.join(', ') }}</p>
        <el-button type="primary" @click="openManualUrl" style="margin-top: 12px;">
          打开{{ platformName(manualGuide.platform) }}发布页
        </el-button>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'

const projects = ref([])
const variants = ref([])
const selectedProjectId = ref('')
const publishConfigs = ref([])
const publishing = ref(false)
const publishResult = ref(null)
const publishHistory = ref([])
const historyLoading = ref(false)
const manualGuideVisible = ref(false)
const manualGuide = ref(null)

const commonTags = ['海鲜', '美食', '源头直发', '新鲜', '挑选技巧', '烹饪', '教程', '探店']

const loadProjects = async () => {
  try {
    const res = await fetch('/api/projects/')
    projects.value = await res.json()
  } catch (e) {
    ElMessage.error('加载项目失败')
  }
}

const loadVariants = async () => {
  if (!selectedProjectId.value) return
  try {
    const res = await fetch(`/api/projects/${selectedProjectId.value}/variants`)
    variants.value = await res.json()

    // 生成发布配置
    publishConfigs.value = variants.value.map(v => ({
      platform: v.platform,
      auto: ['douyin', 'kuaishou'].includes(v.platform),
      title: v.title || '',
      description: v.description || '',
      tags: v.tags || [],
      scheduled_at: null,
      enabled: true,
      variant_id: v.id,
    }))
  } catch (e) {
    ElMessage.error('加载版本失败')
  }
}

const loadHistory = async () => {
  historyLoading.value = true
  try {
    const res = await fetch('/api/publish/tasks')
    publishHistory.value = await res.json()
  } catch (e) {}
  finally {
    historyLoading.value = false
  }
}

const startPublish = async () => {
  const enabledConfigs = publishConfigs.value.filter(c => c.enabled)
  if (!enabledConfigs.length) {
    ElMessage.warning('请至少选择一个平台')
    return
  }

  publishing.value = true
  try {
    const res = await fetch('/api/projects/publish', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project_id: selectedProjectId.value,
        platforms: enabledConfigs.map(c => ({
          platform: c.platform,
          title: c.title,
          description: c.description,
          tags: c.tags,
          scheduled_at: c.scheduled_at ? new Date(c.scheduled_at).toISOString() : null,
        })),
      }),
    })
    const data = await res.json()
    publishResult.value = data

    if (data.status === 'completed') {
      ElMessage.success(data.message)
    }
    loadHistory()
  } catch (e) {
    ElMessage.error('发布失败')
  } finally {
    publishing.value = false
  }
}

const showManualGuide = (row) => {
  // 获取半自动发布指引
  const variant = variants.value.find(v => v.platform === row.platform)
  if (!variant) return

  fetch(`/api/publish/manual-guide/${row.platform}?variant_id=${variant.id}`)
    .then(r => r.json())
    .then(data => {
      manualGuide.value = data
      manualGuideVisible.value = true
    })
}

const openManualUrl = () => {
  if (manualGuide.value?.manual_url) {
    window.open(manualGuide.value.manual_url, '_blank')
  }
}

const platformName = (p) => {
  const map = { douyin: '抖音', kuaishou: '快手', video_channel: '视频号', xiaohongshu: '小红书' }
  return map[p] || p
}

const formatDate = (date) => {
  if (!date) return '-'
  return new Date(date).toLocaleString('zh-CN')
}

onMounted(() => {
  loadProjects()
  loadHistory()
})
</script>

<style scoped>
.publish-page h2 { margin-bottom: 20px; }
.section-card { margin-bottom: 24px; }
</style>
