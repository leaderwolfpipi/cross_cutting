<template>
  <div v-if="p">
    <div class="toolbar">
      <el-button link @click="$router.push('/projects')"><el-icon><ArrowLeft /></el-icon>返回</el-button>
      <h3 style="margin: 0">{{ p.name }}</h3>
      <span class="muted">{{ (p.product.items || []).join('、') }} · {{ p.material_ids.length }} 个素材</span>
      <div class="spacer" />
      <el-select v-model="gen.platforms" multiple style="width: 280px" placeholder="平台">
        <el-option v-for="pl in p.platforms" :key="pl" :label="PLATFORMS[pl]" :value="pl" />
      </el-select>
      <el-input-number v-model="gen.count" :min="1" :max="10" style="width: 110px" />
      <el-button type="primary" :loading="busy" @click="generate"><el-icon><MagicStick /></el-icon>生成混剪</el-button>
      <el-button type="success" @click="$router.push({ path: '/publish', query: { project: p.id } })"><el-icon><Promotion /></el-icon>一键发布</el-button>
    </div>

    <el-tabs v-model="tab">
      <el-tab-pane v-for="pl in ['all', ...p.platforms]" :key="pl" :name="pl" :label="pl === 'all' ? `全部 (${p.variants.length})` : `${PLATFORMS[pl]} (${p.variants.filter((v) => v.platform === pl).length})`" />
    </el-tabs>

    <div class="grid">
      <el-card v-for="v in shown" :key="v.id" shadow="hover" :body-style="{ padding: 0 }" class="vcard">
        <div class="cover" @click="open(v)">
          <img v-if="v.cover_url" :src="v.cover_url" />
          <div v-else class="placeholder">
            <el-icon class="is-loading" v-if="busyStates.includes(v.status)" size="28"><Loading /></el-icon>
            <span>{{ label(VARIANT_STATUS, v.status) }}</span>
          </div>
          <el-tag class="st" :type="color(VARIANT_STATUS, v.status)" effect="dark" size="small">{{ label(VARIANT_STATUS, v.status) }}</el-tag>
          <el-tag class="pl" size="small">{{ v.platform_name }} #{{ v.seq }}</el-tag>
        </div>
        <div class="meta">
          <div class="title" :title="v.copy?.title">{{ v.copy?.title || '…' }}</div>
          <div class="muted">{{ templateName(v.template_id) }} · {{ v.duration || '-' }}s · 相似度 {{ pct(v.dedup_score) }}</div>
          <div v-if="v.error" class="err">{{ v.error }}</div>
        </div>
      </el-card>
    </div>
    <el-empty v-if="!p.variants.length" description="还没有成片：选择平台和数量后点“生成混剪”" />

    <el-drawer v-model="drawer" size="70%" :title="cur ? `${cur.platform_name} #${cur.seq} · ${templateName(cur.template_id)}` : ''">
      <div v-if="cur" class="detail">
        <div class="left">
          <video v-if="cur.video_url" :src="cur.video_url" :poster="cur.cover_url" controls class="player" />
          <div class="muted" style="margin-top: 6px">
            {{ cur.width }}×{{ cur.height }} · {{ cur.duration }}s · {{ fmtSize(cur.size_bytes) }} · 渲染 {{ cur.render_backend }} {{ cur.render_seconds }}s
          </div>
          <div v-if="cur.editor_project_path" class="muted">剪映 me 工程：{{ cur.editor_project_path }}（可用 App 打开精修）</div>
        </div>
        <div class="right">
          <el-form label-position="top">
            <el-form-item label="标题"><el-input v-model="copy.title" maxlength="55" show-word-limit /></el-form-item>
            <el-form-item label="正文"><el-input v-model="copy.description" type="textarea" :rows="3" /></el-form-item>
            <el-form-item label="话题">
              <el-select v-model="copy.hashtags" multiple filterable allow-create style="width: 100%" />
            </el-form-item>
            <el-button @click="saveCopy">保存文案</el-button>
            <el-button type="success" :disabled="!['passed', 'review'].includes(cur.status)" @click="approve">批准发布</el-button>
            <el-button type="danger" plain :disabled="['discarded', 'approved'].includes(cur.status)" @click="discard">作废</el-button>
            <el-button :disabled="!cur.video_url" @click="recheck">重新查重</el-button>
          </el-form>

          <h4>查重报告</h4>
          <template v-if="cur.dedup_report">
            <el-alert :type="color(DECISION, cur.dedup_report.decision)" :closable="false" show-icon
                      :title="`判定：${label(DECISION, cur.dedup_report.decision)} · 综合 ${pct(cur.dedup_report.score)} · 对比 ${cur.dedup_report.compared} 个历史作品`" />
            <el-descriptions :column="2" size="small" border style="margin-top: 8px">
              <el-descriptions-item label="内容重合(max P,V)">{{ pct(cur.dedup_report.metrics.content) }}</el-descriptions-item>
              <el-descriptions-item label="历史库覆盖">{{ pct(cur.dedup_report.metrics.library) }}</el-descriptions-item>
              <el-descriptions-item label="渲染前来源检查">{{ pct(cur.plan_report?.l1?.max_pair) }} · {{ cur.plan_report?.attempts?.length }} 次编排</el-descriptions-item>
              <el-descriptions-item label="有效帧">{{ cur.dedup_report.metrics.informative_frames }}/{{ cur.dedup_report.metrics.frames }}</el-descriptions-item>
            </el-descriptions>
            <el-table :data="cur.dedup_report.matches" size="small" style="margin-top: 8px" empty-text="无相似历史作品">
              <el-table-column label="相似作品" min-width="160">
                <template #default="{ row }">{{ row.title || row.owner_id.slice(0, 8) }} <span class="muted">{{ row.owner_type === 'history' ? '导入' : '' }}</span></template>
              </el-table-column>
              <el-table-column label="来源P" width="70"><template #default="{ row }">{{ pct(row.P) }}</template></el-table-column>
              <el-table-column label="画面V" width="70"><template #default="{ row }">{{ pct(row.V) }}</template></el-table-column>
              <el-table-column label="音频A" width="70"><template #default="{ row }">{{ pct(row.A) }}</template></el-table-column>
              <el-table-column label="重合片段" min-width="150">
                <template #default="{ row }">
                  <span v-for="(s, i) in row.segments.slice(0, 3)" :key="i" class="muted">{{ s.q0 }}–{{ s.q1 }}s↔{{ s.r0 }}–{{ s.r1 }}s </span>
                </template>
              </el-table-column>
            </el-table>
          </template>
          <el-empty v-else :image-size="50" description="尚未查重" />

          <h4>剪辑方案（{{ cur.plan?.clips?.length || 0 }} 个镜头）</h4>
          <el-table :data="cur.plan?.clips || []" size="small" max-height="260">
            <el-table-column prop="role" label="角色" width="80" />
            <el-table-column prop="product" label="产品" width="90" />
            <el-table-column label="源区间" width="110"><template #default="{ row }">{{ row.src_in }}–{{ row.src_out }}s</template></el-table-column>
            <el-table-column label="变速" width="60"><template #default="{ row }">{{ row.speed }}×</template></el-table-column>
            <el-table-column label="构图" width="90"><template #default="{ row }">{{ row.background ? '模糊底' : `${row.transform.scale}×` }}</template></el-table-column>
            <el-table-column label="转场" min-width="90"><template #default="{ row }">{{ row.transition_out?.kind || '硬切' }}</template></el-table-column>
          </el-table>
          <div v-if="cur.plan_report?.render_warnings?.length" class="muted" style="margin-top: 8px">
            ⚠ {{ cur.plan_report.render_warnings.join('；') }}
          </div>
          <div v-if="cur.deliveries?.length">
            <h4>发布记录</h4>
            <div v-for="d in cur.deliveries" :key="d.id">
              <el-tag :type="color(DELIVERY_STATE, d.state)" size="small">{{ d.platform_name }} {{ label(DELIVERY_STATE, d.state) }}</el-tag>
              <el-link v-if="d.share_url" :href="d.share_url" target="_blank" style="margin-left: 8px">{{ d.share_url }}</el-link>
            </div>
          </div>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { color, DECISION, DELIVERY_STATE, fmtSize, label, pct, PLATFORMS, VARIANT_STATUS } from '@/labels'

const route = useRoute()
const p = ref(null)
const templates = ref([])
const tab = ref('all')
const busy = ref(false)
const gen = reactive({ platforms: [], count: 2 })
const busyStates = ['queued', 'planning', 'rendering', 'checking']
const shown = computed(() => (p.value?.variants || []).filter((v) => tab.value === 'all' || v.platform === tab.value).slice().reverse())
const templateName = (id) => templates.value.find((t) => t.id === id)?.name || id || '-'

async function load() {
  p.value = await api.project(route.params.id)
  if (!gen.platforms.length) gen.platforms = [...p.value.platforms]
}
async function generate() {
  busy.value = true
  try {
    const r = await api.generate(p.value.id, { platforms: gen.platforms, count: gen.count })
    ElMessage.success(`已排队 ${r.variants.length} 条成片`)
    await load()
  } finally { busy.value = false }
}

const drawer = ref(false)
const cur = ref(null)
const copy = reactive({ title: '', description: '', hashtags: [] })
async function open(v) {
  cur.value = await api.variant(v.id)
  Object.assign(copy, { title: cur.value.copy?.title || '', description: cur.value.copy?.description || '', hashtags: [...(cur.value.copy?.hashtags || [])] })
  drawer.value = true
}
async function saveCopy() { await api.patchVariant(cur.value.id, copy); ElMessage.success('已保存'); load() }
async function approve() {
  const { value } = await ElMessageBox.prompt('复核意见（选填）', '批准发布', { inputPlaceholder: '如：画面差异足够，可以发' }).catch(() => ({}))
  if (value === undefined) return
  await api.approve(cur.value.id, value || '')
  await open(cur.value); load()
}
async function discard() {
  await ElMessageBox.confirm('作废后该成片不会被发布，也不再计入查重历史。确定？', '作废', { type: 'warning' })
  await api.discard(cur.value.id, '')
  await open(cur.value); load()
}
async function recheck() { await api.recheck(cur.value.id); ElMessage.success('已排队重新查重') }

let t
onMounted(async () => {
  templates.value = await api.templates()
  await load()
  t = setInterval(() => { if (p.value?.variants.some((v) => busyStates.includes(v.status))) load() }, 3000)
})
onUnmounted(() => clearInterval(t))
</script>

<style scoped>
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 14px; }
.cover { position: relative; aspect-ratio: 9 / 14; background: #111; cursor: pointer; overflow: hidden; }
.cover img { width: 100%; height: 100%; object-fit: cover; }
.placeholder { height: 100%; display: flex; flex-direction: column; gap: 8px; align-items: center; justify-content: center; color: #aaa; }
.st { position: absolute; left: 8px; top: 8px; }
.pl { position: absolute; right: 8px; top: 8px; }
.meta { padding: 8px 10px; }
.title { font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.err { color: #f53f3f; font-size: 12px; margin-top: 4px; }
.detail { display: flex; gap: 20px; }
.left { width: 340px; flex-shrink: 0; word-break: break-all; }
.player { width: 100%; max-height: 620px; background: #000; border-radius: 8px; }
.right { flex: 1; min-width: 0; }
</style>
