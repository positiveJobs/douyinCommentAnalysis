// 所有请求都发往后端 FastAPI，前端不接触任何 LLM API Key
const JSON_HEADERS = { 'Content-Type': 'application/json' }

async function handle(response) {
  if (response.ok) {
    const type = response.headers.get('content-type') || ''
    if (type.includes('application/json')) return response.json()
    return response.text()
  }
  let detail = `请求失败（HTTP ${response.status}）`
  try {
    const payload = await response.json()
    if (payload.detail) {
      detail = typeof payload.detail === 'string' ? payload.detail : JSON.stringify(payload.detail)
    }
  } catch (error) {
    // 忽略解析错误，使用默认提示
  }
  throw new Error(detail)
}

export function preflight(file) {
  const form = new FormData()
  form.append('file', file)
  return fetch('/api/tasks/preflight', { method: 'POST', body: form }).then(handle)
}

export function createTask(file, topic) {
  const form = new FormData()
  form.append('file', file)
  if (topic) form.append('topic', topic)
  return fetch('/api/tasks', { method: 'POST', body: form }).then(handle)
}

export function listTasks({ page = 1, pageSize = 10, status = '', q = '' } = {}) {
  const query = new URLSearchParams({ page, page_size: pageSize })
  if (status) query.set('status', status)
  if (q) query.set('q', q)
  return fetch(`/api/tasks?${query}`).then(handle)
}

export function getTask(taskId) {
  return fetch(`/api/tasks/${taskId}`).then(handle)
}

export function getHealth() {
  return fetch('/api/health').then(handle)
}

export function getSummary(taskId) {
  return fetch(`/api/tasks/${taskId}/summary`).then(handle)
}

export function getViewpoints(taskId) {
  return fetch(`/api/tasks/${taskId}/viewpoints`).then(handle)
}

export function getViewpoint(taskId, viewpointId) {
  return fetch(`/api/tasks/${taskId}/viewpoints/${viewpointId}`).then(handle)
}

export function getViewpointComments(taskId, viewpointId, page = 1, pageSize = 20) {
  const query = new URLSearchParams({ page, page_size: pageSize })
  return fetch(`/api/tasks/${taskId}/viewpoints/${viewpointId}/comments?${query}`).then(handle)
}

export function getReport(taskId) {
  return fetch(`/api/tasks/${taskId}/report`).then(handle)
}

// kind: json | markdown | html | package
const DOWNLOAD_PATHS = {
  json: 'download/json',
  markdown: 'download/report',
  html: 'download/html',
  package: 'download/package'
}

export function downloadUrl(taskId, kind = 'html') {
  return `/api/tasks/${taskId}/${DOWNLOAD_PATHS[kind] || DOWNLOAD_PATHS.html}`
}

// 服务端渲染的 HTML 报告（直接在浏览器打开，不受报告体积影响）
export function reportHtmlUrl(taskId) {
  return `/api/tasks/${taskId}/report/html`
}

export function deleteTask(taskId) {
  return fetch(`/api/tasks/${taskId}`, { method: 'DELETE' }).then(handle)
}

export function cancelTask(taskId) {
  return fetch(`/api/tasks/${taskId}/cancel`, { method: 'POST' }).then(handle)
}

export { JSON_HEADERS }
