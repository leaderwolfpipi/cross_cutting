<template>
  <div>
    <el-alert type="info" :closable="false" show-icon style="margin-bottom: 16px"
              title="与自己的历史作品比对：来源重合 P（精确，基于剪辑方案）· 画面时序覆盖 V（镜像/调色/变速/裁剪不变）· 音频覆盖 A。导入外部发过的旧视频后，它们也会参与比对。" />
    <el-tabs v-model="tab">
      <el-tab-pane label="查重记录" name="reports">
        <div class="toolbar">
          <el-radio-group v-model="decision" @change="load">
            <el-radio-button value="">全部</el-radio-button>
            <el-radio-button value="review">待复核</el-radio-button>
            <el-radio-button value="reject">拒绝</el-radio-button>
            <el-radio-button value="pass">通过</el-radio-button>
          </el-radio-group>
        </div>
        <el-table :data="reports">
          <el-table-column label="成片" min-width="220">
            <template #default="{ row }">
              <el-link v-if="row.variant" @click="$router.push(`/projects/${row.variant.project_id}`)">
                {{ row.variant.platform_name }} #{{ row.variant.seq }} · {{ row.variant.copy?.title }}
              </el-link>
            </template>
          </el-table-column>
          <el-table-column label="判定" width="90">
            <template #default="{ row }"><el-tag :type="color(DECISION, row.decision)">{{ label(DECISION, row.decision) }}</el-tag></template>
          </el-table-column>
          <el-table-column label="综合" width="70"><template #default="{ row }">{{ pct(row.score) }}</template></el-table-column>
          <el-table-column label="内容重合" width="90"><template #default="{ row }">{{ pct(row.metrics.content) }}</template></el-table-column>
          <el-table-column label="库覆盖" width="80"><template #default="{ row }">{{ pct(row.metrics.library) }}</template></el-table-column>
          <el-table-column label="最相似" min-width="200">
            <template #default="{ row }">
              <span v-if="row.matches[0]">{{ row.matches[0].title || row.matches[0].owner_id.slice(0, 8) }}
                <span class="muted">P{{ pct(row.matches[0].P) }} V{{ pct(row.matches[0].V) }} A{{ pct(row.matches[0].A) }}</span></span>
            </template>
          </el-table-column>
          <el-table-column label="复核" width="150">
            <template #default="{ row }">
              <span v-if="row.review_decision">{{ row.review_decision === 'approve' ? '已批准' : '已作废' }} <span class="muted">{{ row.review_note }}</span></span>
              <template v-else-if="row.variant && row.variant.status === 'review'">
                <el-button size="small" type="success" link @click="review(row, 'approve')">批准</el-button>
                <el-button size="small" type="danger" link @click="review(row, 'discard')">作废</el-button>
              </template>
            </template>
          </el-table-column>
          <el-table-column label="时间" width="170"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="历史作品库" name="history">
        <div class="toolbar">
          <el-select v-model="hist.platform" placeholder="所属平台（空=全部平台）" clearable style="width: 220px">
            <el-option v-for="(n, k) in PLATFORMS" :key="k" :label="n" :value="k" />
          </el-select>
          <input type="file" accept="video/*" multiple @change="(e) => (hist.files = [...e.target.files])" />
          <el-button type="primary" :disabled="!hist.files.length" :loading="hist.busy" @click="uploadHistory">导入历史作品</el-button>
        </div>
        <el-table :data="history">
          <el-table-column prop="title" label="标题" min-width="200" />
          <el-table-column label="平台" width="100"><template #default="{ row }">{{ PLATFORMS[row.platform] || '全部' }}</template></el-table-column>
          <el-table-column label="状态" width="90"><template #default="{ row }">{{ row.status }}</template></el-table-column>
          <el-table-column prop="duration" label="时长" width="80" />
          <el-table-column label="导入时间" width="170"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
          <el-table-column width="80"><template #default="{ row }"><el-button link type="danger" @click="delHistory(row)">删除</el-button></template></el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '@/api'
import { color, DECISION, fmtTime, label, pct, PLATFORMS } from '@/labels'

const tab = ref('reports')
const decision = ref('')
const reports = ref([])
const history = ref([])
const hist = reactive({ platform: '', files: [], busy: false })

const load = async () => { reports.value = await api.reports({ decision: decision.value || undefined }) }
const loadHistory = async () => { history.value = await api.history() }
async function review(row, action) {
  if (action === 'approve') await api.approve(row.variant_id, '查重中心复核通过')
  else await api.discard(row.variant_id, '查重中心作废')
  load()
}
async function uploadHistory() {
  hist.busy = true
  try {
    for (const f of hist.files) await api.uploadHistory(f, f.name.replace(/\.[^.]+$/, ''), hist.platform).catch(() => null)
    ElMessage.success('已导入，后台正在提取指纹')
    hist.files = []
    loadHistory()
  } finally { hist.busy = false }
}
async function delHistory(row) { await api.deleteHistory(row.id); loadHistory() }
onMounted(() => { load(); loadHistory() })
</script>
