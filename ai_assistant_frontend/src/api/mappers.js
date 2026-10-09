/**
 * 后端字段 -> 前端视图字段的映射。
 *
 * 后端返回的是 SQLAlchemy 模型的原始序列化结果（字段为英文、值为原始类型），
 * 页面里使用的是一套中文语义的视图字段，转换集中放在这里，页面不再关心后端命名。
 *
 * 注意：这里只映射后端**真实返回**的字段。不要再写 `xxx: '—'` 这类占位值 ——
 * 页面会把它当真数据渲染出来（「来源部门」「文档分类」「语料段数」都踩过这个坑，
 * 界面上永远显示一个破折号，像是统计坏了）。后端缺什么就补什么，别在前端编。
 */

import { EMBEDDING, STATUS_TEXT, STORAGE, TYPE_META } from './constants'

const pad = (n) => String(n).padStart(2, '0')

/** ISO 时间 / 时间戳 -> YYYY-MM-DD HH:mm（本地时区），无效值返回空串 */
export function formatTime(value) {
  if (!value) return ''
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 字节 -> 可读大小 */
export function formatSize(bytes) {
  const size = Number(bytes) || 0
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(0)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

/** 取文件名后缀，返回小写且不带点，如 'pdf' */
export function extOf(name = '') {
  const index = name.lastIndexOf('.')
  return index === -1 ? '' : name.slice(index + 1).toLowerCase()
}

/** 由后缀反推展示类型，未知返回原后缀 */
export function typeLabelOf(type) {
  return TYPE_META[type]?.label || type || '未知'
}

/**
 * 后端 KnowledgeFile -> 文档记录视图对象
 * @param {object} raw 后端原始记录
 * @param {'private'|'public'} scope 该记录来自私人库还是公共库
 */
export function mapFileRecord(raw, scope = 'private') {
  const name = raw?.original_name || '未命名文件'
  const type = extOf(name)
  const sizeBytes = Number(raw?.size) || 0
  const status = raw?.status || 'pending'

  return {
    id: raw?.id || '',
    name,
    type,
    size: formatSize(sizeBytes),
    sizeBytes,
    status: STATUS_TEXT[status] || status,
    rawStatus: status,
    // 姓名优先，其次登录账号；都取不到才回退占位符
    // （历史数据可能对应的用户已被删，后端那时会回 null）
    uploader: raw?.uploader_name || raw?.uploader_account || '—',
    uploaderAccount: raw?.uploader_account || '',
    createdAt: formatTime(raw?.created_at),
    rawCreatedAt: raw?.created_at || '',
    reviewer: raw?.reviewer_name || raw?.reviewer_account || '',
    reviewedAt: raw?.reviewed_at ? formatTime(raw.reviewed_at) : '',
    remark: raw?.review_comment || '',
    errorMessage: raw?.error_message || '',
    kbId: raw?.knowledge_base_id || '',
    kbName: scope === 'public' ? '公共知识库' : '我的知识库',
    scope,
    storage: {
      bucket: raw?.bucket || STORAGE.bucket,
      key: raw?.object_key || '',
      contentType: raw?.content_type || 'application/octet-stream',
      sizeBytes,
      uploadedAt: formatTime(raw?.created_at),
      accessedAt: status === 'stored' ? formatTime(raw?.updated_at) : ''
    }
  }
}

/**
 * 后端向量分段 -> 语料视图对象
 * @param {object} raw /knowledge/vector/list 的 items 元素
 */
export function mapVectorItem(raw) {
  const docName = raw?.file_name || '未知文档'
  const chars = Number(raw?.len) || 0
  const scope = raw?.scope || 'private'

  return {
    id: raw?.id || '',
    kbName: scope === 'public' ? '公共知识库' : '我的知识库',
    scope,
    docName,
    docType: extOf(docName),
    // seq 是后端元数据里的 0-based 索引，展示时统一 +1（「第 0 段」对用户很奇怪）
    seq: raw?.seq ?? 0,
    chars,
    vectorId: raw?.id || '',
    model: EMBEDDING.model,
    dim: EMBEDDING.dim,
    createdAt: formatTime(raw?.ingested_at),
    // 保留原始 ISO 时间戳供排序使用：格式化后的文本只精确到分钟，无法区分同分钟内先后
    rawCreatedAt: raw?.ingested_at || '',
    content: raw?.document || ''
  }
}

/**
 * 后端 ConversationVO -> 会话视图对象
 * ConversationVO: { id, user_id, title, created_at, updated_at }
 */
export function mapConversation(raw) {
  return {
    id: raw?.id || '',
    title: raw?.title || '未命名会话',
    createdAt: formatTime(raw?.created_at),
    updatedAt: formatTime(raw?.updated_at),
    // 原始 ISO 供排序/比较使用（展示文本只精确到分钟）
    rawCreatedAt: raw?.created_at || '',
    rawUpdatedAt: raw?.updated_at || ''
  }
}

/**
 * 后端 ConversationMessageVO -> 消息视图对象
 * ConversationMessageVO: { id, conversation_id, role, content, sequence, model,
 *                          status, token_count, parent_id, created_at, updated_at }
 */
export function mapMessage(raw) {
  return {
    id: raw?.id || '',
    conversationId: raw?.conversation_id || '',
    role: raw?.role || 'assistant',
    content: raw?.content || '',
    sequence: raw?.sequence ?? 0,
    model: raw?.model || '',
    status: raw?.status || 'done',
    tokenCount: raw?.token_count ?? null,
    createdAt: formatTime(raw?.created_at),
    rawCreatedAt: raw?.created_at || ''
  }
}
