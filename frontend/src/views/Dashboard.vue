<template>
  <div v-if="d">
    <div class="card-row">
      <el-card v-for="s in stats" :key="s.label" shadow="never">
        <div class="muted">{{ s.label }}</div>
        <div class="num">{{ s.value }}</div>
        <div class="muted">{{ s.sub }}</div>
      </el-card>
    </div>

    <el-row :gutter="16" style="margin-top: 16px">
      <el-col :span="8">
        <el-card shadow="never" header="成片状态">
          <div v-for="(n, k) in d.variant_status" :key="k" class="line">
            <el-tag :type="color(VARIANT_STATUS, k)" size="small">{{ label(VARIANT_STATUS, k) }}</el-tag><b>{{ n }}</b>
          </div>
          <el-empty v-if="!Object.keys(d.variant_status).length" description="还没有成片" :image-size="60" />
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never" header="发布任务">
          <div v-for="(n, k) in d.delivery_states" :key="k" class="line">
            <el-tag :type="color(DELIVERY_STATE, k)" size="small">{{ label(DELIVERY_STATE, k) }}</el-tag><b>{{ n }}</b>
          </div>
          <el-empty v-if="!Object.keys(d.delivery_states).length" description="还没有发布" :image-size="60" />
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never" header="待办">
          <el-alert v-if="d.variant_status.review" type="warning" :closable="false" show-icon
                    :title="`${d.variant_status.review} 条成片待查重复核`" @click="$router.push('/dedup')" class="todo" />
          <el-alert v-if="d.awaiting_operator" type="info" :closable="false" show-icon
                    :title="`${d.awaiting_operator} 个视频号/小红书任务待人工发布`" @click="$router.push('/publish')" class="todo" />
          <el-alert v-if="d.accounts_need_auth" type="error" :closable="false" show-icon
                    :title="`${d.accounts_need_auth} 个账号需要重新授权`" @click="$router.push('/accounts')" class="todo" />
          <el-alert v-if="d.jobs.dead" type="error" :closable="false" show-icon
                    :title="`${d.jobs.dead} 个后台任务失败`" @click="$router.push('/jobs')" class="todo" />
          <el-empty v-if="!d.variant_status.review && !d.awaiting_operator && !d.accounts_need_auth && !d.jobs.dead"
                    description="暂无待办" :image-size="60" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '@/api'
import { color, DELIVERY_STATE, label, VARIANT_STATUS } from '@/labels'

const d = ref(null)
const load = async () => { d.value = await api.dashboard() }
const stats = computed(() => [
  { label: '可用素材', value: d.value.materials_ready, sub: `共 ${d.value.materials} 个` },
  { label: '混剪项目', value: d.value.projects, sub: '' },
  { label: '近7天成片', value: d.value.variants_7d, sub: `累计 ${d.value.variants}` },
  { label: '近7天发布', value: d.value.published_7d, sub: '' },
  { label: '平台账号', value: d.value.accounts, sub: d.value.accounts_need_auth ? `${d.value.accounts_need_auth} 个需授权` : '' },
  { label: '运行中任务', value: (d.value.jobs.running || 0) + (d.value.jobs.queued || 0), sub: `失败 ${d.value.jobs.dead || 0}` },
])
let t
onMounted(() => { load(); t = setInterval(load, 10000) })
onUnmounted(() => clearInterval(t))
</script>

<style scoped>
.num { font-size: 28px; font-weight: 600; margin: 6px 0; }
.line { display: flex; justify-content: space-between; padding: 6px 0; }
.todo { margin-bottom: 8px; cursor: pointer; }
</style>
