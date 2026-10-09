/**
 * HTTP 请求层：统一封装 fetch，负责：
 *  1. 拼接 baseURL（开发环境走 vite 代理 /api，见 vite.config.js）
 *  2. 自动带上 Authorization: Bearer <token>
 *  3. 解包后端统一响应体 BaseResponse { code, message, data }
 *  4. 把 HTTP 错误 / 业务错误转换成统一的 ApiError
 *
 * 后端所有接口的前缀在 main.py 与 knowledge.py 中定义：
 *  - 用户 / 会话：无前缀，如 /users/login、/conversation/chat
 *  - 知识库：前缀 /knowledge，如 /knowledge/file/list
 */

const BASE_URL = import.meta.env.VITE_API_BASE ?? '/api'

/** 流式请求要自己拼 URL（api() 会把响应体读成 json，读不了 SSE） */
export { BASE_URL }

const TOKEN_KEY = 'zhiyu_token'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** 把 FastAPI 的 detail（字符串或 422 校验错误数组）拍平成可读文案 */
function normalizeDetail(detail, fallback) {
  if (!detail) return fallback
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const text = detail
      .map((item) => {
        const field = Array.isArray(item?.loc) ? item.loc[item.loc.length - 1] : ''
        return field ? `${field}: ${item?.msg}` : item?.msg
      })
      .filter(Boolean)
      .join('；')
    return text || fallback
  }
  return typeof detail === 'object' ? JSON.stringify(detail) : String(detail)
}

function buildUrl(path, params) {
  let query = ''
  if (params) {
    const search = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value === undefined || value === null || value === '') return
      search.append(key, value)
    })
    query = search.toString()
  }
  return `${BASE_URL}${path}${query ? `?${query}` : ''}`
}

/**
 * 调用后端并返回解包后的 data。
 * @param {string} path  以 / 开头的接口路径，如 '/users/login'
 * @param {{ method?: string, body?: any, params?: object, isForm?: boolean, signal?: AbortSignal }} options
 */
export async function api(path, options = {}) {
  const { method = 'GET', body, params, isForm = false, signal } = options

  const headers = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  let payload
  if (isForm) {
    // FormData 由浏览器自动设置 multipart 边界，不要手动写 Content-Type
    payload = body
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }

  let response
  try {
    response = await fetch(buildUrl(path, params), { method, headers, body: payload, signal })
  } catch (error) {
    if (error?.name === 'AbortError') throw error
    throw new ApiError('无法连接后端服务，请确认服务已启动', 0)
  }

  const text = await response.text()
  let parsed = null
  if (text) {
    try {
      parsed = JSON.parse(text)
    } catch {
      parsed = text
    }
  }

  if (!response.ok) {
    // 401 说明 token 失效或未登录，清掉本地凭证避免继续带脏 token 发请求
    if (response.status === 401) clearToken()
    const message = normalizeDetail(
      parsed?.detail ?? parsed?.message,
      `请求失败（HTTP ${response.status}）`
    )
    throw new ApiError(message, response.status)
  }

  // 后端统一返回 { code, message, data }，这里直接吐 data 给业务层
  if (parsed && typeof parsed === 'object' && 'data' in parsed) {
    return parsed.data
  }
  return parsed
}

/**
 * 上传文件用的 multipart 请求。
 * @param {object} [options.fields] 除 file 之外要一起提交的表单字段（如 replace=true）
 */
export function upload(path, file, { params, fields } = {}) {
  const form = new FormData()
  form.append('file', file)
  Object.entries(fields || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null) form.append(key, String(value))
  })
  return api(path, { method: 'POST', body: form, isForm: true, params })
}
