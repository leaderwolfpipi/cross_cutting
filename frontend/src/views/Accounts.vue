<template>
  <div>
    <div class="toolbar">
      <el-alert type="info" :closable="false" show-icon style="flex: 1"
                title="抖音/快手：开放平台 OAuth 授权后由接口发布；视频号/小红书没有公开发布接口，使用“人工辅助”模式（系统准备好发布包，运营在手机/后台点最后一下）。" />
      <el-button type="primary" @click="openCreate"><el-icon><Plus /></el-icon>添加账号</el-button>
    </div>
    <el-table :data="items">
      <el-table-column label="平台" width="90"><template #default="{ row }">{{ row.platform_name }}</template></el-table-column>
      <el-table-column prop="display_name" label="名称" min-width="150" />
      <el-table-column label="方式" width="100"><template #default="{ row }">{{ row.mode === 'api' ? '开放接口' : '人工辅助' }}</template></el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }"><el-tag :type="{ active: 'success', needs_auth: 'danger', disabled: 'info' }[row.status]">{{ { active: '正常', needs_auth: '需授权', disabled: '停用' }[row.status] }}</el-tag></template>
      </el-table-column>
      <el-table-column label="令牌" min-width="170">
        <template #default="{ row }">
          <span v-if="row.mode === 'assisted'" class="muted">不需要</span>
          <span v-else-if="row.has_token">有效至 {{ fmtTime(row.token_expires_at) }}</span>
          <span v-else class="err">未授权</span>
        </template>
      </el-table-column>
      <el-table-column label="节流" width="150"><template #default="{ row }">{{ row.daily_limit || '默认' }}条/天 · 间隔{{ row.min_interval_s >= 0 ? row.min_interval_s + 's' : '默认' }}</template></el-table-column>
      <el-table-column label="最近发布" width="170"><template #default="{ row }">{{ fmtTime(row.last_published_at) }}</template></el-table-column>
      <el-table-column label="操作" width="260">
        <template #default="{ row }">
          <el-button v-if="row.mode === 'api'" link type="primary" @click="authorize(row)">OAuth 授权</el-button>
          <el-button link @click="openEdit(row)">编辑</el-button>
          <el-button link @click="toggle(row)">{{ row.status === 'disabled' ? '启用' : '停用' }}</el-button>
          <el-button link type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="visible" :title="form.id ? '编辑账号' : '添加账号'" width="560px">
      <el-form label-width="110px">
        <el-form-item label="平台">
          <el-select v-model="form.platform" :disabled="!!form.id" @change="onPlatform">
            <el-option v-for="(n, k) in PLATFORMS" :key="k" :label="n" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item label="名称"><el-input v-model="form.display_name" /></el-form-item>
        <el-form-item label="发布方式">
          <el-radio-group v-model="form.mode">
            <el-radio value="api" :disabled="!apiPlatforms.includes(form.platform)">开放接口</el-radio>
            <el-radio value="assisted">人工辅助</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="每日上限"><el-input-number v-model="form.daily_limit" :min="0" /> <span class="muted">&nbsp;0 = 默认</span></el-form-item>
        <el-form-item label="最小间隔(秒)"><el-input-number v-model="form.min_interval_s" :min="-1" /> <span class="muted">&nbsp;-1 = 默认</span></el-form-item>
        <template v-if="form.mode === 'api'">
          <el-divider>手动填写令牌（通常用 OAuth 授权，无需填写）</el-divider>
          <el-form-item label="access_token"><el-input v-model="form.access_token" type="password" show-password /></el-form-item>
          <el-form-item label="refresh_token"><el-input v-model="form.refresh_token" type="password" show-password /></el-form-item>
          <el-form-item label="open_id"><el-input v-model="form.open_id" /></el-form-item>
          <el-form-item label="有效期(秒)"><el-input-number v-model="form.expires_in" :min="0" /></el-form-item>
        </template>
        <el-form-item label="备注"><el-input v-model="form.note" type="textarea" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { fmtTime, PLATFORMS } from '@/labels'

const apiPlatforms = ['douyin', 'kuaishou']
const items = ref([])
const visible = ref(false)
const blank = () => ({ id: '', platform: 'douyin', display_name: '', mode: 'api', daily_limit: 0, min_interval_s: -1,
  access_token: '', refresh_token: '', open_id: '', expires_in: 0, note: '' })
const form = reactive(blank())
const load = async () => { items.value = await api.accounts() }
function onPlatform() { if (!apiPlatforms.includes(form.platform)) form.mode = 'assisted' }
function openCreate() { Object.assign(form, blank()); visible.value = true }
function openEdit(row) { Object.assign(form, blank(), row, { access_token: '', refresh_token: '' }); visible.value = true }
async function save() {
  const body = { ...form }
  delete body.id
  if (form.id) await api.updateAccount(form.id, body)
  else await api.createAccount(body)
  visible.value = false
  load()
}
async function authorize(row) {
  const r = await api.authorize(row.platform, row.id)
  window.open(r.url, '_blank', 'width=720,height=760')
  ElMessage.info('在新窗口完成授权后刷新本页')
}
async function toggle(row) { await api.accountStatus(row.id, row.status === 'disabled' ? 'active' : 'disabled'); load() }
async function remove(row) {
  await ElMessageBox.confirm(`删除账号 ${row.display_name}？有发布记录的账号会改为停用并清除令牌。`, '删除', { type: 'warning' })
  const r = await api.deleteAccount(row.id)
  if (r.reason) ElMessage.info(r.reason)
  load()
}
onMounted(load)
</script>

<style scoped>
.err { color: #f53f3f; }
</style>
