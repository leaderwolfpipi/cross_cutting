import axios from 'axios'
import { ElMessage } from 'element-plus'

const KEY = 'svs.apiKey'

export const auth = {
  get key() {
    try { return localStorage.getItem(KEY) || '' } catch { return '' }
  },
  set(key) {
    try { localStorage.setItem(KEY, key) } catch { /* private mode: key lives for this tab only */ }
    http.defaults.headers.common['X-API-Key'] = key
  },
  clear() {
    try { localStorage.removeItem(KEY) } catch { /* ignore */ }
    delete http.defaults.headers.common['X-API-Key']
  },
}

export const http = axios.create({ timeout: 120000 })
if (auth.key) http.defaults.headers.common['X-API-Key'] = auth.key

http.interceptors.response.use(
  (r) => r.data,
  (err) => {
    const status = err.response?.status
    const detail = err.response?.data?.detail
    if (status === 401) {
      auth.clear()
      if (!location.pathname.startsWith('/login')) location.href = '/login'
    } else {
      const msg = typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : err.message
      ElMessage.error({ message: msg, duration: 5000 })
    }
    return Promise.reject(err)
  },
)

export const api = {
  info: () => http.get('/api/system/info'),
  dashboard: () => http.get('/api/dashboard'),
  jobs: (params) => http.get('/api/jobs', { params }),
  job: (id) => http.get(`/api/jobs/${id}`),
  retryJob: (id) => http.post(`/api/jobs/${id}/retry`),

  materials: (params) => http.get('/api/materials', { params }),
  material: (id) => http.get(`/api/materials/${id}`),
  uploadMaterial: (file, product, license_type, onUploadProgress) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('product', product || '')
    fd.append('license_type', license_type || 'self_shot')
    return http.post('/api/materials/upload', fd, { onUploadProgress, timeout: 0 })
  },
  importMaterials: (body) => http.post('/api/materials/import', body),
  patchMaterial: (id, body) => http.patch(`/api/materials/${id}`, body),
  reanalyze: (id) => http.post(`/api/materials/${id}/reanalyze`),
  describeMaterials: (body = {}) => http.post('/api/materials/describe', body),
  deleteMaterial: (id) => http.delete(`/api/materials/${id}`),

  bgm: () => http.get('/api/bgm'),
  uploadBgm: (file, name, license_note) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('name', name || '')
    fd.append('license_note', license_note)
    return http.post('/api/bgm/upload', fd, { timeout: 0 })
  },
  history: () => http.get('/api/history'),
  uploadHistory: (file, title, platform) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('title', title || '')
    fd.append('platform', platform || '')
    return http.post('/api/history/upload', fd, { timeout: 0 })
  },
  deleteHistory: (id) => http.delete(`/api/history/${id}`),

  templates: () => http.get('/api/templates'),
  projects: () => http.get('/api/projects'),
  project: (id) => http.get(`/api/projects/${id}`),
  createProject: (body) => http.post('/api/projects', body),
  updateProject: (id, body) => http.patch(`/api/projects/${id}`, body),
  generate: (id, body) => http.post(`/api/projects/${id}/generate`, body),

  quoteSheets: () => http.get('/api/quotes'),
  quoteSheet: (id) => http.get(`/api/quotes/${id}`),
  importQuote: (source_text) => http.post('/api/quotes', { source_text }),
  importLatestQuote: () => http.post('/api/quotes/import-latest'),
  editQuoteEntry: (id, eid, body) => http.patch(`/api/quotes/${id}/entries/${eid}`, body),
  acknowledgeQuoteCorrection: (id, cid, review_note) => http.post(`/api/quotes/${id}/corrections/${cid}/acknowledge`, { review_note }),
  confirmQuote: (id, body) => http.post(`/api/quotes/${id}/confirm`, body),
  previewQuote: (id, category) => http.get(`/api/quotes/${id}/categories/${encodeURIComponent(category)}/preview`),
  exportQuote: (id, category) => http.post(`/api/quotes/${id}/categories/${encodeURIComponent(category)}/export`, null, { timeout: 0 }),
  preflightQuotePublish: (id, body) => http.post(`/api/quotes/${id}/publish/preflight`, body),
  publishQuoteSeries: (id, body) => http.post(`/api/quotes/${id}/publish`, body),

  variants: (params) => http.get('/api/variants', { params }),
  variant: (id) => http.get(`/api/variants/${id}`),
  patchVariant: (id, body) => http.patch(`/api/variants/${id}`, body),
  approve: (id, note) => http.post(`/api/variants/${id}/approve`, { note }),
  discard: (id, note) => http.post(`/api/variants/${id}/discard`, { note }),
  recheck: (id) => http.post(`/api/variants/${id}/recheck`),
  reports: (params) => http.get('/api/dedup/reports', { params }),

  accounts: () => http.get('/api/accounts'),
  createAccount: (body) => http.post('/api/accounts', body),
  updateAccount: (id, body) => http.put(`/api/accounts/${id}`, body),
  accountStatus: (id, status) => http.post(`/api/accounts/${id}/status`, null, { params: { status } }),
  deleteAccount: (id) => http.delete(`/api/accounts/${id}`),
  authorize: (platform, account_id) => http.get(`/api/accounts/oauth/${platform}/authorize`, { params: { account_id } }),

  preflight: (body) => http.post('/api/publish/preflight', body),
  oneClick: (body) => http.post('/api/publish/one-click', body),
  batches: () => http.get('/api/publish/batches'),
  batch: (id) => http.get(`/api/publish/batches/${id}`),
  deliveries: (params) => http.get('/api/publish/deliveries', { params }),
  delivery: (id) => http.get(`/api/publish/deliveries/${id}`),
  confirm: (id, body) => http.post(`/api/publish/deliveries/${id}/confirm`, body),
  retryDelivery: (id) => http.post(`/api/publish/deliveries/${id}/retry`),
  cancelDelivery: (id) => http.post(`/api/publish/deliveries/${id}/cancel`),
}
