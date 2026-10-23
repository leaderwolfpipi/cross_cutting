<template>
  <div>
    <div class="toolbar">
      <el-radio-group v-model="status" @change="load">
        <el-radio-button value="">全部</el-radio-button>
        <el-radio-button value="queued,running">进行中</el-radio-button>
        <el-radio-button value="dead,failed">失败</el-radio-button>
        <el-radio-button value="succeeded">完成</el-radio-button>
      </el-radio-group>
      <el-select v-model="queueName" clearable placeholder="队列" style="width: 120px" @change="load">
        <el-option v-for="q in ['analyze', 'render', 'dedup', 'publish']" :key="q" :label="q" :value="q" />
      </el-select>
    </div>
    <el-table :data="items" size="small">
      <el-table-column prop="id" label="#" width="70" />
      <el-table-column prop="queue" label="队列" width="80" />
      <el-table-column prop="kind" label="类型" width="160" />
      <el-table-column label="状态" width="90">
        <template #default="{ row }"><el-tag :type="{ succeeded: 'success', running: 'warning', queued: 'info', dead: 'danger', failed: 'danger' }[row.status]" size="small">{{ row.status }}</el-tag></template>
      </el-table-column>
      <el-table-column label="进度" width="150">
        <template #default="{ row }"><el-progress :percentage="Math.round(row.progress * 100)" :stroke-width="6" /></template>
      </el-table-column>
      <el-table-column label="信息" min-width="240">
        <template #default="{ row }"><span>{{ row.message }}</span><div v-if="row.last_error" class="err">{{ row.last_error.split('\n')[0] }}</div></template>
      </el-table-column>
      <el-table-column prop="attempts" label="次数" width="60" />
      <el-table-column label="计划执行" width="170"><template #default="{ row }">{{ fmtTime(row.run_after) }}</template></el-table-column>
      <el-table-column width="70">
        <template #default="{ row }"><el-button v-if="row.status === 'dead'" link type="primary" @click="retry(row)">重试</el-button></template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { api } from '@/api'
import { fmtTime } from '@/labels'

const items = ref([])
const status = ref('')
const queueName = ref('')
const load = async () => { items.value = await api.jobs({ status: status.value || undefined, queue_name: queueName.value || undefined }) }
async function retry(row) { await api.retryJob(row.id); load() }
let t
onMounted(() => { load(); t = setInterval(load, 4000) })
onUnmounted(() => clearInterval(t))
</script>

<style scoped>
.err { color: #f53f3f; font-size: 12px; }
</style>
