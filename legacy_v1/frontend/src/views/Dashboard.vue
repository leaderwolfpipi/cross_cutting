<template>
  <div class="dashboard">
    <h2>工作台</h2>

    <div class="stats-row">
      <el-card class="stat-card" shadow="hover">
        <div class="stat-icon" style="background: #e6f7ff;">
          <el-icon :size="32" color="#1890ff"><Folder /></el-icon>
        </div>
        <div class="stat-info">
          <div class="stat-value">{{ stats.materials || 0 }}</div>
          <div class="stat-label">素材数量</div>
        </div>
      </el-card>

      <el-card class="stat-card" shadow="hover">
        <div class="stat-icon" style="background: #f6ffed;">
          <el-icon :size="32" color="#52c41a"><VideoCamera /></el-icon>
        </div>
        <div class="stat-info">
          <div class="stat-value">{{ stats.projects || 0 }}</div>
          <div class="stat-label">混剪项目</div>
        </div>
      </el-card>

      <el-card class="stat-card" shadow="hover">
        <div class="stat-icon" style="background: #fff7e6;">
          <el-icon :size="32" color="#faad14"><Search /></el-icon>
        </div>
        <div class="stat-info">
          <div class="stat-value">{{ stats.fingerprints || 0 }}</div>
          <div class="stat-label">指纹记录</div>
        </div>
      </el-card>

      <el-card class="stat-card" shadow="hover">
        <div class="stat-icon" style="background: #f9f0ff;">
          <el-icon :size="32" color="#722ed1"><Promotion /></el-icon>
        </div>
        <div class="stat-info">
          <div class="stat-value">{{ stats.published || 0 }}</div>
          <div class="stat-label">已发布</div>
        </div>
      </el-card>
    </div>

    <div class="quick-actions">
      <h3>快捷操作</h3>
      <div class="action-grid">
        <el-button type="primary" size="large" @click="$router.push('/materials')">
          <el-icon><Upload /></el-icon> 上传素材
        </el-button>
        <el-button type="success" size="large" @click="$router.push('/projects')">
          <el-icon><VideoCamera /></el-icon> 新建混剪
        </el-button>
        <el-button type="warning" size="large" @click="$router.push('/dedup')">
          <el-icon><Search /></el-icon> 查重检测
        </el-button>
        <el-button type="danger" size="large" @click="$router.push('/publish')">
          <el-icon><Promotion /></el-icon> 一键发布
        </el-button>
      </div>
    </div>

    <div class="platform-status">
      <h3>平台发布能力</h3>
      <el-table :data="platforms" stripe>
        <el-table-column prop="name" label="平台" width="120" />
        <el-table-column label="API支持" width="100">
          <template #default="{ row }">
            <el-tag :type="row.has_api ? 'success' : 'info'">
              {{ row.has_api ? '✓ 有' : '✗ 无' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="自动发布" width="100">
          <template #default="{ row }">
            <el-tag :type="row.auto ? 'success' : 'warning'">
              {{ row.auto ? '✓ 支持' : '人工' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="note" label="说明" />
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const stats = ref({})
const platforms = ref([
  { name: '抖音', has_api: true, auto: false, note: '需要OAuth授权 + 人工确认' },
  { name: '快手', has_api: true, auto: true, note: '完整API支持自动发布' },
  { name: '视频号', has_api: false, auto: false, note: '通过视频号助手半自动发布' },
  { name: '小红书', has_api: false, auto: false, note: '通过创作中心半自动发布' },
])

onMounted(async () => {
  try {
    const [materials, projects, dedup] = await Promise.all([
      fetch('/api/materials/').then(r => r.json()),
      fetch('/api/projects/').then(r => r.json()),
      fetch('/api/dedup/stats').then(r => r.json()),
    ])
    stats.value = {
      materials: materials.length || 0,
      projects: projects.length || 0,
      fingerprints: dedup.total_fingerprints || 0,
      published: 0,
    }
  } catch (e) {
    console.error('Failed to load stats:', e)
  }
})
</script>

<style scoped>
.dashboard h2 {
  margin-bottom: 24px;
  color: #262626;
}

.stats-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 32px;
}

.stat-card {
  display: flex;
  align-items: center;
  padding: 8px;
}

.stat-icon {
  width: 56px;
  height: 56px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-right: 16px;
}

.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: #262626;
}

.stat-label {
  font-size: 13px;
  color: #8c8c8c;
  margin-top: 4px;
}

.quick-actions {
  margin-bottom: 32px;
}

.quick-actions h3,
.platform-status h3 {
  margin-bottom: 16px;
  color: #262626;
  font-size: 16px;
}

.action-grid {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}

.platform-status {
  background: #fff;
  padding: 24px;
  border-radius: 8px;
}
</style>
