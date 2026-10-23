<template>
  <div>
    <el-card shadow="never" header="一键发布">
      <div class="toolbar">
        <el-select v-model="form.project_id" filterable placeholder="选择项目" style="width: 280px">
          <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
        </el-select>
        <el-checkbox-group v-model="form.platforms">
          <el-checkbox v-for="(n, k) in PLATFORMS" :key="k" :value="k">{{ n }}</el-checkbox>
        </el-checkbox-group>
        <el-checkbox v-model="form.skip_failed">跳过未通过预检的目标</el-checkbox>
        <div class="spacer" />
        <el-button :disabled="!form.project_id" :loading="checking" @click="preflight">预检</el-button>
        <el-button type="danger" :disabled="!form.project_id" :loading="publishing" @click="publish"><el-icon><Promotion /></el-icon>一键发布</el-button>
      </div>
      <p class="muted">系统为每个可用账号挑选该项目中“查重通过/已批准、且未发过该账号、与历史最不相似”的成片；先全量预检，全部通过才开始上传（避免发一半）。抖音/快手走开放接口，视频号/小红书生成人工发布任务。</p>
      <el-table v-if="pre" :data="pre.targets" size="small">
        <el-table-column label="平台" width="90"><template #default="{ row }">{{ PLATFORMS[row.platform] }}</template></el-table-column>
        <el-table-column label="方式" width="90"><template #default="{ row }">{{ row.mode === 'api' ? '接口' : '人工辅助' }}</template></el-table-column>
        <el-table-column label="标题" min-width="200"><template #default="{ row }">{{ row.copy.title }}</template></el-table-column>
        <el-table-column label="预检" width="80"><template #default="{ row }"><el-tag :type="row.ok ? 'success' : 'danger'">{{ row.ok ? '通过' : '阻断' }}</el-tag></template></el-table-column>
        <el-table-column label="检查项" min-width="360">
          <template #default="{ row }">
            <el-tooltip v-for="c in row.checks" :key="c.code" :content="c.message">
              <el-tag :type="c.ok ? 'success' : c.level === 'warn' ? 'warning' : 'danger'" size="small" effect="plain" style="margin: 2px">{{ c.code }}</el-tag>
            </el-tooltip>
            <div v-for="c in row.checks.filter((x) => !x.ok)" :key="'m' + c.code" class="err">{{ c.message }}</div>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" style="margin-top: 16px" v-if="manual.length">
      <template #header>待人工发布（视频号 / 小红书） <el-tag type="primary" size="small">{{ manual.length }}</el-tag></template>
      <div class="manual">
        <div v-for="d in manual" :key="d.id" class="task">
          <img :src="d.variant?.cover_url" class="mcover" />
          <div class="mbody">
            <b>{{ d.platform_name }}</b> · {{ d.title }}
            <el-input :model-value="d.text" type="textarea" :rows="3" readonly style="margin: 6px 0" />
            <div class="toolbar" style="margin: 0">
              <el-button size="small" @click="copyText(d.text)">复制正文</el-button>
              <el-button size="small" tag="a" :href="d.handoff?.video_url" download>下载视频</el-button>
              <el-button size="small" tag="a" :href="d.handoff?.cover_url" download>下载封面</el-button>
              <el-button size="small" tag="a" :href="d.handoff?.creator_url" target="_blank">打开创作后台</el-button>
            </div>
            <div class="toolbar" style="margin: 8px 0 0">
              <el-input v-model="confirmForm[d.id].url" size="small" placeholder="发布后的作品链接" style="width: 260px" />
              <el-input v-model="confirmForm[d.id].op" size="small" placeholder="操作人" style="width: 110px" />
              <el-button size="small" type="primary" @click="confirm(d)">确认已发布</el-button>
            </div>
          </div>
          <div class="qr">
            <canvas :ref="(el) => drawQr(el, d.handoff?.mobile_url)" />
            <div class="muted">手机扫码：保存视频、复制文案</div>
          </div>
        </div>
      </div>
    </el-card>

    <el-card shadow="never" style="margin-top: 16px" header="发布记录">
      <el-table :data="batches" row-key="id" size="small">
        <el-table-column type="expand">
          <template #default="{ row }">
            <el-table :data="row.deliveries" size="small" @row-click="openDelivery">
              <el-table-column label="平台" width="90"><template #default="{ row: d }">{{ d.platform_name }}</template></el-table-column>
              <el-table-column label="状态" width="120"><template #default="{ row: d }"><el-tag :type="color(DELIVERY_STATE, d.state)" size="small">{{ label(DELIVERY_STATE, d.state) }}</el-tag></template></el-table-column>
              <el-table-column label="标题" min-width="200"><template #default="{ row: d }">{{ d.title }}</template></el-table-column>
              <el-table-column label="重试" width="60"><template #default="{ row: d }">{{ d.attempts }}</template></el-table-column>
              <el-table-column label="作品" min-width="200"><template #default="{ row: d }"><el-link v-if="d.share_url" :href="d.share_url" target="_blank">{{ d.share_url }}</el-link><span v-else class="err">{{ d.last_error }}</span></template></el-table-column>
            </el-table>
          </template>
        </el-table-column>
        <el-table-column label="批次" width="110"><template #default="{ row }">{{ row.id.slice(0, 8) }}</template></el-table-column>
        <el-table-column label="状态" width="120"><template #default="{ row }"><el-tag :type="batchColor(row.status)">{{ BATCH[row.status] || row.status }}</el-tag></template></el-table-column>
        <el-table-column label="进度" min-width="260">
          <template #default="{ row }">
            <el-tag v-for="d in row.deliveries" :key="d.id" :type="color(DELIVERY_STATE, d.state)" size="small" style="margin-right: 4px">{{ d.platform_name }} {{ label(DELIVERY_STATE, d.state) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="170"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
      </el-table>
    </el-card>

    <el-drawer v-model="drawer" size="45%" :title="cur ? `${cur.platform_name} · ${label(DELIVERY_STATE, cur.state)}` : ''">
      <template v-if="cur">
        <div class="toolbar">
          <el-button v-if="['failed', 'needs_auth', 'platform_rejected', 'submitted'].includes(cur.state)" type="primary" @click="retry">重试</el-button>
          <el-button v-if="['queued', 'uploading', 'uploaded', 'awaiting_operator', 'needs_auth', 'failed'].includes(cur.state)" @click="cancel">取消</el-button>
          <el-link v-if="cur.share_url" :href="cur.share_url" target="_blank">{{ cur.share_url }}</el-link>
        </div>
        <el-timeline>
          <el-timeline-item v-for="(e, i) in cur.events" :key="i" :timestamp="fmtTime(e.at)" :type="e.to === 'published' ? 'success' : ['failed', 'needs_auth'].includes(e.to) ? 'danger' : 'primary'">
            <b>{{ label(DELIVERY_STATE, e.to) }}</b> {{ e.message }}
          </el-timeline-item>
        </el-timeline>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import QRCode from 'qrcode'
import { api } from '@/api'
import { color, DELIVERY_STATE, fmtTime, label, PLATFORMS } from '@/labels'

const route = useRoute()
const projects = ref([])
const batches = ref([])
const manual = ref([])
const confirmForm = reactive({})
const form = reactive({ project_id: route.query.project || '', platforms: Object.keys(PLATFORMS), skip_failed: false })
const pre = ref(null)
const checking = ref(false)
const publishing = ref(false)
const BATCH = { running: '进行中', completed: '全部完成', partial: '部分成功', failed: '失败', preflight_failed: '预检未通过' }
const batchColor = (s) => ({ completed: 'success', partial: 'warning', failed: 'danger', preflight_failed: 'danger' }[s] || 'primary')

async function load() {
  batches.value = await api.batches()
  const waiting = await api.deliveries({ state: 'awaiting_operator' })
  manual.value = await Promise.all(waiting.map((d) => api.delivery(d.id)))
  for (const d of manual.value) confirmForm[d.id] ||= { url: '', op: '' }
}
async function preflight() {
  checking.value = true
  try { pre.value = await api.preflight({ project_id: form.project_id, platforms: form.platforms }) } finally { checking.value = false }
}
async function publish() {
  publishing.value = true
  try {
    const b = await api.oneClick({ project_id: form.project_id, platforms: form.platforms, skip_failed: form.skip_failed })
    if (b.status === 'preflight_failed') {
      pre.value = { targets: b.preflight }
      ElMessage.warning('预检未通过，未发布任何内容（可勾选“跳过未通过预检的目标”）')
    } else ElMessage.success(`已创建 ${b.deliveries.length} 个发布任务`)
    load()
  } finally { publishing.value = false }
}
function drawQr(el, url) { if (el && url) QRCode.toCanvas(el, url, { width: 120, margin: 1 }) }
function copyText(t) { navigator.clipboard?.writeText(t); ElMessage.success('已复制') }
async function confirm(d) {
  const f = confirmForm[d.id]
  if (!f.url || !f.op) return ElMessage.warning('请填写作品链接和操作人')
  await api.confirm(d.id, { post_url: f.url, operator: f.op })
  ElMessage.success('已确认')
  load()
}

const drawer = ref(false)
const cur = ref(null)
async function openDelivery(d) { cur.value = await api.delivery(d.id); drawer.value = true }
async function retry() { await api.retryDelivery(cur.value.id); cur.value = await api.delivery(cur.value.id); load() }
async function cancel() { await api.cancelDelivery(cur.value.id); cur.value = await api.delivery(cur.value.id); load() }

let t
onMounted(async () => { projects.value = await api.projects(); load(); t = setInterval(load, 5000) })
onUnmounted(() => clearInterval(t))
</script>

<style scoped>
.err { color: #f53f3f; font-size: 12px; }
.manual { display: flex; flex-direction: column; gap: 12px; }
.task { display: flex; gap: 14px; padding: 10px; border: 1px solid #eee; border-radius: 8px; }
.mcover { width: 96px; height: 150px; object-fit: cover; border-radius: 6px; background: #111; }
.mbody { flex: 1; min-width: 0; }
.qr { text-align: center; width: 130px; }
</style>
