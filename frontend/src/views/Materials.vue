<template>
  <div>
    <div class="toolbar">
      <el-select v-model="filter.product" clearable placeholder="产品" style="width: 140px" @change="load">
        <el-option v-for="p in products" :key="p" :label="p" :value="p" />
      </el-select>
      <el-select v-model="filter.status" clearable placeholder="状态" style="width: 120px" @change="load">
        <el-option v-for="(v, k) in MATERIAL_STATUS" :key="k" :label="v[0]" :value="k" />
      </el-select>
      <div class="spacer" />
      <el-button title="让视觉模型逐镜头记录画面，文案据此生成" @click="describeAll">补全视觉场记</el-button>
      <el-button @click="importVisible = true"><el-icon><FolderOpened /></el-icon>服务器目录导入</el-button>
      <el-button type="primary" @click="uploadVisible = true"><el-icon><Upload /></el-icon>上传素材</el-button>
    </div>

    <div class="grid">
      <el-card v-for="m in items" :key="m.id" shadow="hover" :body-style="{ padding: '0' }" @click="open(m)" class="mat">
        <div class="thumb">
          <img v-if="m.thumb_url" :src="m.thumb_url" />
          <el-icon v-else size="32"><Film /></el-icon>
          <el-tag class="st" :type="color(MATERIAL_STATUS, m.status)" size="small">{{ label(MATERIAL_STATUS, m.status) }}</el-tag>
          <el-tag v-if="m.hdr" class="hdr" size="small" type="warning">HDR</el-tag>
        </div>
        <div class="meta">
          <div class="name" :title="m.filename">{{ m.filename }}</div>
          <div class="muted">{{ m.product || '未标注产品' }} · {{ m.duration }}s · {{ m.width }}×{{ m.height }} · {{ m.shot_count }} 镜头</div>
          <div class="muted">{{ LICENSES[m.license_type] }}</div>
        </div>
      </el-card>
    </div>
    <el-empty v-if="!items.length" description="素材库为空：上传或从服务器目录导入海鲜视频" />

    <el-dialog v-model="uploadVisible" title="上传素材" width="520px">
      <el-form label-width="80px">
        <el-form-item label="产品"><el-input v-model="up.product" placeholder="如：红帝王蟹" /></el-form-item>
        <el-form-item label="授权">
          <el-select v-model="up.license_type"><el-option v-for="(v, k) in LICENSES" :key="k" :label="v" :value="k" /></el-select>
        </el-form-item>
        <el-form-item label="文件">
          <input type="file" multiple accept="video/*" @change="(e) => (up.files = [...e.target.files])" />
        </el-form-item>
        <el-progress v-if="up.busy" :percentage="up.progress" />
      </el-form>
      <template #footer>
        <el-button @click="uploadVisible = false">取消</el-button>
        <el-button type="primary" :loading="up.busy" :disabled="!up.files.length" @click="doUpload">上传 {{ up.files.length }} 个</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="importVisible" title="从服务器目录导入（不复制文件）" width="560px">
      <el-input v-model="imp.path" placeholder="服务器上的文件夹绝对路径（须在 IMPORT_ROOTS 内）" />
      <el-checkbox v-model="imp.product_from_folder" style="margin-top: 10px">用文件夹名作为产品名（如 红帝王蟹/xx.mp4）</el-checkbox>
      <template #footer>
        <el-button type="primary" :disabled="!imp.path" @click="doImport">导入</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="drawer" size="60%" :title="cur?.filename">
      <template v-if="cur">
        <video v-if="cur.proxy_url" :src="cur.proxy_url" controls class="preview" />
        <el-descriptions :column="3" border size="small" style="margin: 12px 0">
          <el-descriptions-item label="时长">{{ cur.duration }}s</el-descriptions-item>
          <el-descriptions-item label="分辨率">{{ cur.width }}×{{ cur.height }} @ {{ cur.fps }}fps</el-descriptions-item>
          <el-descriptions-item label="HDR">{{ cur.hdr || '否' }}（已转 SDR 代理）</el-descriptions-item>
          <el-descriptions-item label="来源分组">{{ cur.source_group.slice(0, 8) }}</el-descriptions-item>
          <el-descriptions-item label="被成片使用">{{ cur.used_in_variants }} 次</el-descriptions-item>
          <el-descriptions-item label="错误">{{ cur.error || '-' }}</el-descriptions-item>
        </el-descriptions>
        <el-form inline>
          <el-form-item label="产品"><el-input v-model="edit.product" style="width: 140px" /></el-form-item>
          <el-form-item label="授权">
            <el-select v-model="edit.license_type" style="width: 160px"><el-option v-for="(v, k) in LICENSES" :key="k" :label="v" :value="k" /></el-select>
          </el-form-item>
          <el-button type="primary" @click="save">保存</el-button>
          <el-button @click="toggleBlack">{{ cur.status === 'blacklisted' ? '取消拉黑' : '拉黑' }}</el-button>
          <el-button @click="reanalyze">重新分析</el-button>
          <el-button type="danger" plain @click="remove">删除</el-button>
        </el-form>
        <h4>镜头（{{ cur.shots.length }}）</h4>
        <div class="shots">
          <div v-for="s in cur.shots" :key="s.id" class="shot">
            <img :src="s.thumb_url" />
            <div class="muted">{{ s.start.toFixed(1) }}–{{ s.end.toFixed(1) }}s · 吸睛 {{ pct(s.eye_catch) }}</div>
            <div class="muted">运动 {{ pct(s.motion) }} · 鲜艳 {{ pct(s.vivid) }} · 清晰 {{ pct(s.sharpness) }}</div>
          </div>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { color, label, LICENSES, MATERIAL_STATUS, pct } from '@/labels'

const items = ref([])
const filter = reactive({ product: '', status: '' })
const products = computed(() => [...new Set(items.value.map((m) => m.product).filter(Boolean))])
const load = async () => { items.value = await api.materials({ product: filter.product || undefined, status: filter.status || undefined }) }

const uploadVisible = ref(false)
const up = reactive({ product: '', license_type: 'self_shot', files: [], busy: false, progress: 0 })
async function doUpload() {
  up.busy = true
  try {
    for (const [i, f] of up.files.entries()) {
      await api.uploadMaterial(f, up.product, up.license_type, (e) => {
        up.progress = Math.round(((i + (e.loaded / (e.total || 1))) / up.files.length) * 100)
      }).catch(() => null)
    }
    ElMessage.success('上传完成，后台正在分析')
    uploadVisible.value = false
    up.files = []
    load()
  } finally { up.busy = false; up.progress = 0 }
}

const importVisible = ref(false)
const imp = reactive({ path: '', product_from_folder: true })
async function doImport() {
  const r = await api.importMaterials({ paths: [imp.path], product_from_folder: imp.product_from_folder })
  ElMessage.success(`导入 ${r.created.length} 个，跳过 ${r.skipped.length} 个`)
  importVisible.value = false
  load()
}

const drawer = ref(false)
const cur = ref(null)
const edit = reactive({ product: '', license_type: '' })
async function open(m) {
  cur.value = await api.material(m.id)
  Object.assign(edit, { product: cur.value.product, license_type: cur.value.license_type })
  drawer.value = true
}
async function save() { await api.patchMaterial(cur.value.id, edit); ElMessage.success('已保存'); load() }
async function toggleBlack() {
  await api.patchMaterial(cur.value.id, { blacklisted: cur.value.status !== 'blacklisted' })
  cur.value = await api.material(cur.value.id); load()
}
async function describeAll() {
  const r = await api.describeMaterials()
  ElMessage.success(r.queued.length ? `已排队 ${r.queued.length} 个素材的视觉场记` : '所有素材的视觉场记都是最新的')
}
async function reanalyze() { await api.reanalyze(cur.value.id); ElMessage.success('已排队重新分析'); load() }
async function remove() {
  await ElMessageBox.confirm('删除后不可恢复，确定？', '删除素材', { type: 'warning' })
  await api.deleteMaterial(cur.value.id)
  drawer.value = false
  load()
}

let t
onMounted(() => { load(); t = setInterval(() => { if (items.value.some((m) => ['uploaded', 'analyzing'].includes(m.status))) load() }, 4000) })
onUnmounted(() => clearInterval(t))
</script>

<style scoped>
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 14px; }
.mat { cursor: pointer; }
.thumb { position: relative; height: 240px; background: #111; display: flex; align-items: center; justify-content: center; color: #666; }
.thumb img { width: 100%; height: 100%; object-fit: cover; }
.st { position: absolute; left: 8px; top: 8px; }
.hdr { position: absolute; right: 8px; top: 8px; }
.meta { padding: 8px 10px; }
.name { font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.preview { max-height: 420px; max-width: 100%; background: #000; border-radius: 6px; }
.shots { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; }
.shot img { width: 100%; border-radius: 4px; }
</style>
