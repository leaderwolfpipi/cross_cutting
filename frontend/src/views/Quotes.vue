<template>
  <div class="quotes">
    <el-alert title="每天把供应商报价原文保存为 backend/app/quotes/latest.txt，或在同目录 source.json 的 url 填写 http(s) 地址，然后点「导入最新报价」。url 非空时优先读网址。只有当日报价、待复核项已处理并由操作人确认后，才能导出。" type="info" :closable="false" show-icon />
    <div class="toolbar" style="margin-top:18px">
      <el-select v-model="selectedId" placeholder="选择报价单" style="width:340px" @change="loadSheet">
        <el-option v-for="s in sheets" :key="s.id" :value="s.id" :label="`${s.quote_date} · ${s.supplier} · ${s.count} 条`" />
      </el-select>
      <el-button @click="importOpen = true">粘贴新报价单</el-button>
      <el-button @click="importLatest">导入最新报价</el-button>
      <span class="spacer" />
      <el-tag v-if="sheet" :type="sheet.current ? 'success' : 'warning'">{{ sheet.current ? '当天报价' : '历史报价' }}</el-tag>
      <el-tag v-if="sheet" :type="sheet.review_count ? 'danger' : 'success'">待复核 {{ sheet.review_count }}</el-tag>
    </div>

    <template v-if="sheet">
      <el-card shadow="never" class="meta">
        <b>{{ sheet.supplier }}</b><span>{{ sheet.quote_date }} · {{ sheet.unit }} · {{ sheet.count }} 条规格</span>
        <span v-if="sheet.confirmed_by">已由 {{ sheet.confirmed_by }} 确认</span>
        <span v-else>尚未确认对外展示</span>
        <el-button link type="primary" @click="sourceOpen = true">查看原文与更正记录</el-button>
      </el-card>
      <el-tabs v-model="category" @tab-change="loadPreview">
        <el-tab-pane v-for="c in sheet.categories" :key="c.name" :name="c.name">
          <template #label>{{ c.name }} <el-badge v-if="c.review_count" :value="c.review_count" class="tab-badge" /></template>
        </el-tab-pane>
      </el-tabs>
      <div v-if="current" class="series">
        <div class="card-preview">
          <img v-if="preview?.image_url" :src="preview.image_url" alt="报价视频首页预览" />
          <div class="muted">共 {{ preview?.pages || 0 }} 页 · 每页停留约 5 秒 · 四行一页，可暂停截图</div>
        </div>
        <div class="content">
          <div class="toolbar">
            <b>{{ category }}</b><span class="muted">{{ current.count }} 条 · {{ current.products.join('、') }}</span>
            <span class="spacer" />
            <el-button type="primary" :loading="exporting" :disabled="!canExport" @click="exportCurrent">导出该品类视频与长图</el-button>
          </div>
          <el-alert v-if="!canExport" :title="blockReason" type="warning" :closable="false" style="margin-bottom:12px" />
          <el-table :data="rows" max-height="600" stripe size="small">
            <el-table-column prop="product" label="商品" min-width="140" />
            <el-table-column prop="spec" label="规格／状态" min-width="140" />
            <el-table-column prop="qualifier" label="附加报价" min-width="125" />
            <el-table-column label="报价" width="100"><template #default="{row}"><b>{{ row.price }} {{ sheet.unit }}</b></template></el-table-column>
            <el-table-column label="核对" min-width="160"><template #default="{row}"><el-tag :type="row.status === 'ready' ? 'success' : 'danger'" size="small">{{ row.status === 'ready' ? '已解析' : '待复核' }}</el-tag><span class="muted" style="margin-left:6px">{{ row.review_note }}</span></template></el-table-column>
            <el-table-column label="原文行" width="75" prop="source_line" />
            <el-table-column label="操作" width="75"><template #default="{row}"><el-button link type="primary" @click="openEdit(row)">复核</el-button></template></el-table-column>
          </el-table>
          <el-card v-if="exported" shadow="never" style="margin-top:14px">
            <div class="toolbar"><b>发布配文</b><el-select v-model="copyPlatform" style="width:150px"><el-option label="抖音" value="douyin" /><el-option label="快手" value="kuaishou" /><el-option label="视频号" value="channels" /><el-option label="小红书" value="xiaohongshu" /></el-select></div>
            <p><b>{{ exported.platform_copy[copyPlatform].title }}</b></p>
            <p style="white-space:pre-wrap">{{ exported.platform_copy[copyPlatform].caption }}</p>
            <el-button @click="copyText">复制标题与正文</el-button>
            <el-button tag="a" :href="exported.video_url" target="_blank" type="primary">下载视频</el-button>
            <el-button tag="a" :href="exported.image_url" target="_blank">下载完整长图</el-button>
            <el-button tag="a" :href="exported.cover_url" target="_blank">下载封面</el-button>
          </el-card>
        </div>
      </div>
      <el-card shadow="never" style="margin-top:18px">
        <b>发布前确认</b>
        <p class="muted">确认这份表可作为供应商对外参考报价展示，并记录核对人和依据。修改任何报价后需要重新确认。</p>
        <div class="toolbar">
          <el-input v-model="operator" placeholder="核对人姓名" style="width:180px" />
          <el-input v-model="confirmNote" placeholder="核对依据，例如：与供应商当天原表逐项核对" style="width:420px" />
          <el-button type="primary" :disabled="!sheet.current || !!sheet.review_count || !operator || confirmNote.length < 4" @click="confirm">确认当日报价</el-button>
        </div>
      </el-card>
    </template>

    <el-dialog v-model="importOpen" title="导入供应商报价原文" width="760px">
      <el-input v-model="sourceText" type="textarea" :rows="18" placeholder="粘贴包含供应商名称、日期、单位和【商品】规格=价格的报价单" />
      <template #footer><el-button @click="importOpen = false">取消</el-button><el-button type="primary" :loading="importing" @click="importText">导入并解析</el-button></template>
    </el-dialog>
    <el-dialog v-model="sourceOpen" title="原始报价与更正" width="760px">
      <el-alert v-if="sheet?.corrections?.length" :title="`识别出 ${sheet.corrections.length} 条更正，其中 ${sheet.corrections.filter(c => !c.matched).length} 条未匹配原始规格`" type="warning" :closable="false" />
      <el-table v-if="sheet?.corrections?.length" :data="sheet.corrections" size="small" style="margin-top:12px">
        <el-table-column prop="product" label="商品" width="130" />
        <el-table-column prop="spec" label="规格" width="100" />
        <el-table-column label="更正" min-width="130"><template #default="{row}">{{ row.old_price ?? '原行缺失' }} → {{ row.new_price }} <span class="muted">{{ row.raw }}</span></template></el-table-column>
        <el-table-column label="状态" width="120"><template #default="{row}"><el-tag :type="row.acknowledged ? 'success' : 'warning'" size="small">{{ row.acknowledged ? '已记录' : '待说明' }}</el-tag></template></el-table-column>
        <el-table-column label="操作" width="100"><template #default="{row}"><el-button v-if="!row.acknowledged" link type="primary" @click="acknowledge(row)">说明并归档</el-button></template></el-table-column>
      </el-table>
      <pre class="source">{{ sheet?.source_text }}</pre>
    </el-dialog>
    <el-dialog v-model="editOpen" title="复核报价行" width="520px">
      <p class="muted">原文：{{ editing?.raw }} · 第 {{ editing?.source_line }} 行</p>
      <el-form label-width="90px">
        <el-form-item label="商品">{{ editing?.product }}</el-form-item>
        <el-form-item label="规格"><el-input v-model="edit.spec" /></el-form-item>
        <el-form-item label="报价"><el-input-number v-model="edit.price" :min="0.01" :precision="2" /> {{ sheet?.unit }}</el-form-item>
        <el-form-item label="附加报价"><el-input v-model="edit.qualifier" placeholder="如 双须价；保留原文含义" /></el-form-item>
        <el-form-item label="状态"><el-radio-group v-model="edit.status"><el-radio value="ready">已核对</el-radio><el-radio value="review">继续待核对</el-radio></el-radio-group></el-form-item>
        <el-form-item label="核对依据"><el-input v-model="edit.review_note" type="textarea" :rows="3" placeholder="标明批次、规格含义或供应商更正依据" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="editOpen = false">取消</el-button><el-button type="primary" @click="saveEdit">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api'

const sheets = ref([]), selectedId = ref(''), sheet = ref(null), category = ref(''), preview = ref(null)
const sourceText = ref(''), importOpen = ref(false), sourceOpen = ref(false), importing = ref(false)
const editOpen = ref(false), editing = ref(null), edit = reactive({ spec: '', price: 0, qualifier: '', status: 'review', review_note: '' })
const operator = ref(''), confirmNote = ref(''), exporting = ref(false), exported = ref(null)
const copyPlatform = ref('douyin')
const current = computed(() => sheet.value?.categories.find(c => c.name === category.value))
const rows = computed(() => sheet.value?.entries.filter(e => e.category === category.value) || [])
const canExport = computed(() => sheet.value?.current && sheet.value?.confirmed_by && !current.value?.review_count)
const blockReason = computed(() => !sheet.value?.current ? '历史报价仅供核对；请导入当天新表。' : sheet.value.review_count ? '请逐项处理待复核报价后确认整张表。' : !sheet.value.confirmed_by ? '请先填写核对人和依据，确认对外展示。' : '')

async function refresh(id = selectedId.value) {
  sheets.value = await api.quoteSheets()
  const next = id || sheets.value[0]?.id
  if (next) { selectedId.value = next; await loadSheet(next) }
}
async function loadSheet(id) {
  sheet.value = await api.quoteSheet(id)
  category.value = sheet.value.categories.some(c => c.name === category.value) ? category.value : sheet.value.categories[0]?.name || ''
  exported.value = null
  await loadPreview()
}
async function loadPreview() {
  preview.value = null; exported.value = null
  if (sheet.value && category.value) preview.value = await api.previewQuote(sheet.value.id, category.value)
}
async function importText() {
  importing.value = true
  try { const s = await api.importQuote(sourceText.value); importOpen.value = false; sourceText.value = ''; await refresh(s.id) }
  finally { importing.value = false }
}
async function importLatest() { const s = await api.importLatestQuote(); await refresh(s.id) }
function openEdit(row) { editing.value = row; Object.assign(edit, { spec: row.spec, price: row.price, qualifier: row.qualifier, status: row.status, review_note: row.review_note }); editOpen.value = true }
async function saveEdit() { await api.editQuoteEntry(sheet.value.id, editing.value.id, edit); editOpen.value = false; await loadSheet(sheet.value.id); ElMessage.success('已保存，整张表需要重新确认') }
async function acknowledge(row) {
  try {
    const { value } = await ElMessageBox.prompt('原报价主条目未出现在这份表中，请注明向供应商核实的结果；这条更正不会凭空生成报价行。', '更正记录说明', { inputPattern: /\S{4,}/, inputErrorMessage: '请写明核实依据' })
    await api.acknowledgeQuoteCorrection(sheet.value.id, row.id, value)
    await loadSheet(sheet.value.id)
  } catch (err) { if (err !== 'cancel' && err !== 'close') throw err }
}
async function confirm() { await api.confirmQuote(sheet.value.id, { operator: operator.value, note: confirmNote.value }); await loadSheet(sheet.value.id); ElMessage.success('当日报价已确认') }
async function exportCurrent() { exporting.value = true; try { exported.value = await api.exportQuote(sheet.value.id, category.value); ElMessage.success('视频与长图已导出') } finally { exporting.value = false } }
async function copyText() { const item = exported.value.platform_copy[copyPlatform.value]; await navigator.clipboard.writeText(`${item.title}\n${item.caption}`); ElMessage.success('配文已复制') }
onMounted(() => refresh())
</script>

<style scoped>
.quotes { max-width: 1500px; margin: auto; }
.meta { margin-top: 8px; }
.meta :deep(.el-card__body) { display: flex; gap: 24px; align-items: center; flex-wrap: wrap; }
.series { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 22px; align-items: start; }
.card-preview img { width: 100%; border-radius: 12px; box-shadow: 0 12px 30px #16303a24; }
.card-preview .muted { line-height: 1.6; margin-top: 8px; }
.content { min-width: 0; }
.content .toolbar { align-items: baseline; }
.source { white-space: pre-wrap; max-height: 60vh; overflow: auto; font-size: 13px; line-height: 1.5; }
.tab-badge { margin-left: 6px; }
@media(max-width: 850px) { .series { grid-template-columns: 1fr; } .card-preview { max-width: 280px; } }
</style>
