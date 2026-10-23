<template>
  <div class="dedup-page">
    <h2>查重检测中心</h2>

    <el-row :gutter="16" class="stats-cards" style="margin-bottom: 24px;">
      <el-col :span="6">
        <el-card>
          <el-statistic title="指纹总数" :value="stats.total_fingerprints || 0" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card>
          <el-statistic title="检测次数" :value="stats.total_checks || 0" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card>
          <el-statistic title="通过率" :value="stats.pass_rate || 0" suffix="%" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card>
          <el-statistic title="需复核" :value="stats.review_count || 0" />
        </el-card>
      </el-col>
    </el-row>

    <div class="action-bar" style="margin-bottom: 16px;">
      <el-button type="primary" @click="loadResults">
        <el-icon><Refresh /></el-icon> 刷新
      </el-button>
      <el-select v-model="decisionFilter" placeholder="筛选决策" clearable style="width: 120px; margin-left: 12px;">
        <el-option label="通过" value="pass" />
        <el-option label="复核" value="review" />
        <el-option label="拒绝" value="reject" />
      </el-select>
    </div>

    <el-table :data="filteredResults" stripe v-loading="loading">
      <el-table-column prop="id" label="记录ID" width="280" show-overflow-tooltip />
      <el-table-column prop="source_id" label="源视频" width="280" show-overflow-tooltip />
      <el-table-column prop="target_id" label="对比视频" width="280" show-overflow-tooltip />
      <el-table-column label="决策分" width="100">
        <template #default="{ row }">
          <span :style="{ color: row.decision_score > 0.75 ? '#f5222d' : '#52c41a' }">
            {{ (row.decision_score * 100).toFixed(1) }}%
          </span>
        </template>
      </el-table-column>
      <el-table-column label="片段覆盖" width="100">
        <template #default="{ row }">{{ (row.segment_coverage * 100).toFixed(1) }}%</template>
      </el-table-column>
      <el-table-column label="视觉" width="80">
        <template #default="{ row }">{{ (row.visual_score * 100).toFixed(0) }}%</template>
      </el-table-column>
      <el-table-column label="音频" width="80">
        <template #default="{ row }">{{ (row.audio_score * 100).toFixed(0) }}%</template>
      </el-table-column>
      <el-table-column label="决策" width="80">
        <template #default="{ row }">
          <el-tag :type="row.decision === 'pass' ? 'success' : row.decision === 'reject' ? 'danger' : 'warning'" size="small">
            {{ decisionText(row.decision) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="检测时间" width="180">
        <template #default="{ row }">{{ formatDate(row.checked_at) }}</template>
      </el-table-column>
    </el-table>

    <div class="info-box" style="margin-top: 32px;">
      <el-alert
        title="查重检测说明"
        type="info"
        :closable="false"
        description="系统采用多层指纹检测：全片感知哈希 + 帧级时序指纹 + 片段覆盖率 + 音频指纹。综合决策分 S = 0.50×片段覆盖 + 0.25×视觉 + 0.25×音频。≥0.75判定为重复，0.55~0.75需人工复核，<0.55放行。"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'

const results = ref([])
const stats = ref({})
const loading = ref(false)
const decisionFilter = ref('')

const filteredResults = computed(() => {
  if (!decisionFilter.value) return results.value
  return results.value.filter(r => r.decision === decisionFilter.value)
})

const loadResults = async () => {
  loading.value = true
  try {
    const res = await fetch('/api/dedup/results')
    results.value = await res.json()
  } catch (e) {
    ElMessage.error('加载失败')
  } finally {
    loading.value = false
  }
}

const loadStats = async () => {
  try {
    const res = await fetch('/api/dedup/stats')
    stats.value = await res.json()
  } catch (e) {}
}

const decisionText = (d) => {
  const map = { pass: '通过', review: '复核', reject: '拒绝' }
  return map[d] || d
}

const formatDate = (date) => {
  if (!date) return '-'
  return new Date(date).toLocaleString('zh-CN')
}

onMounted(() => {
  loadResults()
  loadStats()
})
</script>

<style scoped>
.dedup-page h2 { margin-bottom: 20px; }
</style>
