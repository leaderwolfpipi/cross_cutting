// Display labels and tag colours shared by the views.
export const PLATFORMS = {
  douyin: '抖音',
  kuaishou: '快手',
  channels: '视频号',
  xiaohongshu: '小红书',
}

export const VARIANT_STATUS = {
  queued: ['排队中', 'info'],
  planning: ['编排中', 'warning'],
  rendering: ['渲染中', 'warning'],
  checking: ['查重中', 'warning'],
  passed: ['查重通过', 'success'],
  review: ['待复核', 'warning'],
  rejected: ['查重拒绝', 'danger'],
  approved: ['已批准', 'success'],
  discarded: ['已作废', 'info'],
  failed: ['失败', 'danger'],
  exhausted: ['素材不足', 'danger'],
}

export const DELIVERY_STATE = {
  queued: ['排队', 'info'],
  uploading: ['上传中', 'warning'],
  uploaded: ['已上传', 'warning'],
  creating: ['提交中', 'warning'],
  create_unknown: ['对账中', 'warning'],
  submitted: ['平台处理中', 'warning'],
  published: ['已发布', 'success'],
  platform_rejected: ['平台拒绝', 'danger'],
  awaiting_operator: ['待人工发布', 'primary'],
  needs_auth: ['需重新授权', 'danger'],
  failed: ['失败', 'danger'],
  canceled: ['已取消', 'info'],
}

export const MATERIAL_STATUS = {
  uploaded: ['待分析', 'info'],
  analyzing: ['分析中', 'warning'],
  ready: ['可用', 'success'],
  failed: ['失败', 'danger'],
  blacklisted: ['已拉黑', 'info'],
}

export const LICENSES = {
  self_shot: '自拍',
  purchased: '采购',
  authorized: '授权',
  unknown: '未知（禁止发布）',
}

export const DECISION = { pass: ['通过', 'success'], review: ['复核', 'warning'], reject: ['拒绝', 'danger'] }

export const label = (map, key) => (map[key] || [key, 'info'])[0]
export const color = (map, key) => (map[key] || [key, 'info'])[1]
export const pct = (x) => (x == null ? '-' : `${Math.round(x * 100)}%`)
export const fmtTime = (s) => (s ? new Date(s).toLocaleString('zh-CN', { hour12: false }) : '-')
export const fmtSize = (b) => (b ? (b > 1048576 ? `${(b / 1048576).toFixed(1)}MB` : `${(b / 1024).toFixed(0)}KB`) : '-')
