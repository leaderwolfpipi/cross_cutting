<template>
  <div>
    <div class="toolbar">
      <div class="spacer" />
      <el-button type="primary" @click="openCreate"><el-icon><Plus /></el-icon>新建混剪项目</el-button>
    </div>
    <el-table :data="items" @row-click="(r) => $router.push(`/projects/${r.id}`)" style="cursor: pointer">
      <el-table-column prop="name" label="项目" min-width="180" />
      <el-table-column label="产品" min-width="160">
        <template #default="{ row }">{{ (row.product.items || []).join('、') || row.product.name }}</template>
      </el-table-column>
      <el-table-column label="平台" min-width="180">
        <template #default="{ row }"><el-tag v-for="p in row.platforms" :key="p" size="small" style="margin-right: 4px">{{ PLATFORMS[p] }}</el-tag></template>
      </el-table-column>
      <el-table-column label="素材" width="70"><template #default="{ row }">{{ row.material_ids.length }}</template></el-table-column>
      <el-table-column label="成片" min-width="200">
        <template #default="{ row }">
          <span v-for="(n, k) in row.variant_counts" :key="k" style="margin-right: 6px">
            <el-tag :type="color(VARIANT_STATUS, k)" size="small">{{ label(VARIANT_STATUS, k) }} {{ n }}</el-tag>
          </span>
        </template>
      </el-table-column>
      <el-table-column label="创建" width="170"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
    </el-table>

    <el-dialog v-model="visible" title="新建混剪项目" width="680px" class="create-dialog">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="96px" scroll-to-error @submit.prevent>
        <el-form-item label="项目名称" prop="name"><el-input v-model="form.name" placeholder="如：今日到港 · 帝王蟹专场" clearable /></el-form-item>
        <el-form-item label="素材" prop="material_ids">
          <el-select v-model="form.material_ids" multiple filterable style="width: 100%" placeholder="选择已分析完成的素材">
            <el-option-group v-for="(list, prod) in grouped" :key="prod" :label="prod">
              <el-option v-for="m in list" :key="m.id" :label="`${m.filename} (${m.duration}s)`" :value="m.id" />
            </el-option-group>
          </el-select>
          <el-button link type="primary" @click="form.material_ids = ready.map((m) => m.id)">全选 {{ ready.length }} 个</el-button>
        </el-form-item>
        <el-form-item label="产品">
          <el-select v-model="form.product.items" multiple filterable allow-create style="width: 100%" placeholder="出镜产品，如 红帝王蟹" >
            <el-option v-for="p in productNames" :key="p" :label="p" :value="p" />
          </el-select>
        </el-form-item>
        <el-form-item label="卖点">
          <el-select v-model="form.product.selling_points" multiple filterable allow-create style="width: 100%"
                     placeholder="回车添加：当天到港 / 鲜活现捞 / 支持加工" />
        </el-form-item>
        <el-row>
          <el-col :span="12"><el-form-item label="门店"><el-input v-model="form.product.store" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="城市"><el-input v-model="form.product.city" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="产地"><el-input v-model="form.product.origin" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="平台">
          <el-checkbox-group v-model="form.platforms">
            <el-checkbox v-for="(n, k) in PLATFORMS" :key="k" :value="k">{{ n }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="叙事模板">
          <el-select v-model="form.options.templates" multiple clearable style="width: 100%" placeholder="不选 = 按平台自动轮换">
            <el-option v-for="t in templates" :key="t.id" :label="t.name" :value="t.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="声音">
          <el-radio-group v-model="form.options.audio_mode">
            <el-radio value="auto">自动（原声/BGM 轮换）</el-radio><el-radio value="original">原声</el-radio><el-radio value="bgm">BGM</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="核实报价">
          <el-select v-model="form.options.quote_sheet_id" clearable style="width: 100%"
                     :placeholder="availableSheets.length ? '可选：关联当天已确认报价，生成带价格的混剪' : '暂无可关联的报价单'"
                     :disabled="!availableSheets.length" @change="loadQuoteSheet">
            <el-option v-for="s in availableSheets" :key="s.id" :value="s.id" :label="`${s.quote_date} · ${s.supplier}`" />
          </el-select>
          <div v-if="!availableSheets.length" class="muted hint">
            {{ sheetHint }}<el-button link type="primary" @click="$router.push('/quotes')">去报价页确认</el-button>
          </div>
        </el-form-item>
        <template v-if="form.options.quote_sheet_id">
          <el-form-item label="报价行" prop="options.quote_entry_id">
            <el-select v-model="form.options.quote_entry_id" filterable style="width: 100%" placeholder="选择与素材商品一致的单一规格报价">
              <el-option v-for="e in matchedQuoteEntries" :key="e.id" :value="e.id"
                         :label="`${e.product} · ${e.spec} · ${e.price} ${selectedQuoteSheet?.unit || ''}`" />
            </el-select>
            <div v-if="!matchedQuoteEntries.length" class="muted hint">{{ entriesHint }}</div>
          </el-form-item>
          <el-form-item label="报价位置">
            <el-radio-group v-model="form.options.quote_placement">
              <el-radio value="auto">按叙事轮换</el-radio><el-radio value="early">前段展示</el-radio><el-radio value="late">前段预告、后段详示</el-radio>
            </el-radio-group>
          </el-form-item>
          <div class="muted" style="margin: -8px 0 16px 96px">仅能使用当天已核实报价；过期或变更后会阻止发布。</div>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="create">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { api } from '@/api'
import { color, fmtTime, label, PLATFORMS, VARIANT_STATUS } from '@/labels'

const router = useRouter()
const items = ref([])
const templates = ref([])
const materials = ref([])
const quoteSheets = ref([])
const selectedQuoteSheet = ref(null)
const availableSheets = computed(() => quoteSheets.value.filter((s) => s.current && s.confirmed_by && !s.review_count))
const ready = computed(() => materials.value.filter((m) => m.status === 'ready'))
const grouped = computed(() => ready.value.reduce((acc, m) => ((acc[m.product || '未标注'] ||= []).push(m), acc), {}))
const productNames = computed(() => [...new Set(ready.value.map((m) => m.product).filter(Boolean))])
const matchedQuoteEntries = computed(() => {
  const products = new Set(ready.value.filter((m) => form.material_ids.includes(m.id)).map((m) => m.product))
  return (selectedQuoteSheet.value?.entries || []).filter((e) => e.status === 'ready' && !e.qualifier && products.has(e.product))
})
const formRef = ref(null)
const creating = ref(false)
const rules = {
  name: [{ required: true, whitespace: true, message: '请填写项目名称', trigger: ['blur', 'change'] }],
  material_ids: [{ type: 'array', required: true, min: 1, message: '请至少选择 1 个素材', trigger: 'change' }],
  'options.quote_entry_id': [{ required: true, message: '已关联报价单，请选择报价行', trigger: 'change' }],
}
// 「核实报价」为什么没得选：报价单必须"当天 + 待复核清零 + 已确认"才允许关联
const sheetHint = computed(() => {
  if (!quoteSheets.value.length) return '还没有导入过报价单。先到报价页导入当天报价、清零待复核行并确认，才能关联。'
  const blocked = quoteSheets.value
    .filter((s) => !(s.current && s.confirmed_by && !s.review_count))
    .slice(0, 2)
    .map((s) => {
      const why = []
      if (!s.current) why.push('不是当天')
      if (s.review_count) why.push(`${s.review_count} 行待复核`)
      if (!s.confirmed_by) why.push('未确认')
      return `${s.quote_date}（${why.join(' · ')}）`
    })
  return `没有可关联的报价单（需当天 + 待复核清零 + 已确认）。最近几份：${blocked.join('；')}。`
})
const entriesHint = computed(() => {
  if (!form.options.quote_sheet_id || matchedQuoteEntries.value.length) return ''
  const prods = [...new Set(ready.value.filter((m) => form.material_ids.includes(m.id)).map((m) => m.product).filter(Boolean))]
  const who = prods.length ? `所选素材的产品「${prods.join('、')}」` : '所选素材（产品未标注）'
  return `这份报价单里没有${who}对应的已解析、无附加报价的行，换素材产品或换一份报价单。`
})
const visible = ref(false)
const blank = () => ({ name: '', material_ids: [], platforms: Object.keys(PLATFORMS),
  product: { name: '海鲜', items: [], selling_points: [], store: '', city: '', origin: '', price: '' },
  options: { templates: [], audio_mode: 'auto', quote_sheet_id: '', quote_entry_id: '', quote_placement: 'auto' } })
const form = reactive(blank())

async function openCreate() {
  Object.assign(form, blank())
  selectedQuoteSheet.value = null
  ;[materials.value, templates.value, quoteSheets.value] = await Promise.all([api.materials(), api.templates(), api.quoteSheets()])
  visible.value = true
}
async function loadQuoteSheet(id) {
  form.options.quote_entry_id = ''
  selectedQuoteSheet.value = id ? await api.quoteSheet(id) : null
}
async function create() {
  const ok = await formRef.value.validate().catch(() => false)
  if (!ok) {
    ElMessage.warning('请补全标红的必填项')
    return // el-form 会把缺的项标红并滚动到第一个出错位置
  }
  creating.value = true
  try {
    const p = await api.createProject(form)
    visible.value = false
    router.push(`/projects/${p.id}`)
  } finally {
    creating.value = false
  }
}
onMounted(async () => { items.value = await api.projects() })
</script>

<style>
/* 滚动收进 body，标题栏与底部按钮常驻——否则必填项会被滚出视野 */
.create-dialog { display: flex; flex-direction: column; max-height: calc(100vh - 96px); }
.create-dialog .el-dialog__header { flex: none; }
.create-dialog .el-dialog__body { flex: 1 1 auto; overflow-y: auto; }
.create-dialog .el-dialog__footer { flex: none; }
.create-dialog .hint { width: 100%; line-height: 1.6; margin-top: 4px; }
</style>
