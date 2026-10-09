/**
 * 知识库接口，对应后端 knowledge.py（router prefix = /knowledge）。
 *
 * 后端接口清单：
 *  POST /knowledge/admin/upload     管理员上传到公共知识库（multipart，字段名 file），状态 pending
 *  POST /knowledge/user/upload      普通用户上传到私人知识库（multipart，file + replace），上传即解析入库
 *  GET  /knowledge/file/conflict    上传前探测同名冲突（参数 name）
 *  GET  /knowledge/file/{id}/preview 审核前预览正文（抽取后的纯文本，不写向量库）
 *  GET  /knowledge/file/{id}/download 下载原始文件（后端从对象存储转发）
 *  DELETE /knowledge/file/{id}      删除文档：向量分段 + 文件记录 + MinIO 对象三处一起清
 *  POST /knowledge/review/{file_id} 管理员审核，请求体 { approved: bool, comment }
 *  GET  /knowledge/file/list        分页文件列表 -> { private_file_list, public_file_list? }
 *  GET  /knowledge/vector/list      分页语料分段 -> { total, page, page_size, items }
 *  GET  /knowledge/tags             统计 -> { stored_count, rejected_count, pending_count, vector_count, ... }
 */

import { ApiError, BASE_URL, api, getToken, upload } from './request'
import { mapFileRecord, mapVectorItem } from './mappers'

/**
 * 文件列表（服务端分页 + 状态筛选 + 关键字搜索）。
 *
 * 单一口径：由 scope 决定查私人库还是公共库，响应带 total 供前端渲染分页器。
 * page / page_size 会下推到数据库，前端只取当页，不要全量拉回来再切片。
 *
 * @param {{ page?: number, pageSize?: number, scope?: 'private'|'public',
 *           status?: string, keyword?: string }} options
 * @returns {{ total, page, pageSize, scope, items }}
 */
export async function listFiles({ page = 1, pageSize = 20, scope = 'private', status, keyword } = {}) {
  const data = await api('/knowledge/file/list', {
    params: { page, page_size: pageSize, scope, status, keyword }
  })
  return {
    total: Number(data?.total) || 0,
    page: Number(data?.page) || page,
    pageSize: Number(data?.page_size) || pageSize,
    scope: data?.scope || scope,
    items: (data?.items || []).map((item) => mapFileRecord(item, scope))
  }
}

/**
 * 普通用户上传到自己的私人知识库，上传后立即解析入库。
 * @param {File} file
 * @param {{ replace?: boolean }} [options] replace=true 表示确认替换同名旧文档
 */
export function uploadPrivateFile(file, { replace = false } = {}) {
  return upload('/knowledge/user/upload', file, {
    fields: replace ? { replace: true } : {}
  })
}

/**
 * 上传前探测同名冲突，用于决定要不要弹「是否替换」。
 * 只查当前用户自己的私人知识库（公共库有审核流程，不做自动替换）。
 * @returns {Promise<{ conflict: boolean, files: Array<{id, originalName, status, size, createdAt}> }>}
 */
export async function checkFileConflict(name) {
  const data = await api('/knowledge/file/conflict', { params: { name } })
  return {
    conflict: Boolean(data?.conflict),
    files: (data?.files || []).map((f) => ({
      id: f.id,
      originalName: f.original_name,
      status: f.status,
      size: f.size,
      createdAt: f.created_at
    }))
  }
}

/**
 * 审核前预览文档正文（后端抽取出的纯文本）。
 *
 * 为什么要有这个接口：待审核的文件**还没解析过**，向量库里没有它的分段，
 * 正文只能由后端现从对象存储取。审核要判断「内容是否合规」，
 * 而文件名/大小/Content-Type 支撑不了这个判断。
 *
 * 注意 available=false 不是异常：解析失败本身就是审核依据 ——
 * 连解析都失败，通过审核也只会得到一条 failed 记录。
 * @returns {Promise<{ available, text, totalChars, truncated, error }>}
 */
export async function previewFile(fileId, { maxChars = 4000 } = {}) {
  const data = await api(`/knowledge/file/${fileId}/preview`, { params: { max_chars: maxChars } })
  return {
    available: data?.available !== false,
    text: data?.text || '',
    totalChars: Number(data?.total_chars) || 0,
    truncated: Boolean(data?.truncated),
    error: data?.error || null
  }
}

/**
 * 取原始文件的二进制内容，由调用方触发下载。
 *
 * 不能直接用 <a href>：接口要带 Authorization 头，普通链接带不上。
 * 也不能用 MinIO 的 presigned URL —— 那要求浏览器能直连 MinIO。
 * @returns {Promise<Blob>}
 */
export async function fetchFileBlob(fileId) {
  const res = await fetch(`${BASE_URL}/knowledge/file/${fileId}/download`, {
    headers: { Authorization: `Bearer ${getToken()}` }
  })
  if (!res.ok) throw new ApiError(`下载失败（HTTP ${res.status}）`, res.status)
  return res.blob()
}

/**
 * 删除一份文档：向量分段、文件记录、MinIO 对象会被一起清掉，不可恢复。
 * 公共库文件仅管理员可删，私人库文件本人或管理员可删。
 * @returns {Promise<{ id, originalName, deletedSegments, storageRemoved }>}
 */
export async function deleteFile(fileId) {
  const data = await api(`/knowledge/file/${fileId}`, { method: 'DELETE' })
  return {
    id: data?.id || fileId,
    originalName: data?.original_name || '',
    deletedSegments: Number(data?.deleted_segments) || 0,
    // 后端删 MinIO 对象失败时为 false（分段与记录仍已删除）
    storageRemoved: data?.storage_removed !== false
  }
}

/** 管理员上传到公共知识库，落库为待审核 */
export function uploadPublicFile(file) {
  return upload('/knowledge/admin/upload', file)
}

/**
 * 审核单个文件。
 * @param {string} fileId 文件 id（UUID）
 * @param {{ approved: boolean, comment?: string }} payload
 */
export function reviewFile(fileId, { approved, comment = '' }) {
  return api(`/knowledge/review/${fileId}`, {
    method: 'POST',
    body: { approved, comment }
  })
}

/** 批量审核：后端没有批量接口，这里串行调用，保证单个失败不影响其余文件 */
export async function batchReviewFile(fileIds, { approved, comment = '' }) {
  const results = []
  for (const fileId of fileIds) {
    results.push(await reviewFile(fileId, { approved, comment }))
  }
  return results
}

/**
 * 语料分段（服务端分页 + 搜索 + 筛选）。
 *
 * page / page_size 会下推到数据库的 OFFSET/LIMIT，keyword / document / scope 也由
 * 数据库执行，响应里的 total 就是同一套筛选条件下的总行数 —— 前端只取当页，
 * 用 total 渲染分页器即可，不要把全量数据拉回来在前端切片。
 *
 * @returns {{ total, totalChars, page, pageSize, documents, items }}
 *   documents 是当前筛选范围内出现过的文档名，用于渲染「来源文档」下拉
 */
export async function listVectors({
  page = 1,
  pageSize = 20,
  keyword,
  document,
  scope,
  contentType,
  segmentId
} = {}) {
  const data = await api('/knowledge/vector/list', {
    params: {
      page,
      page_size: pageSize,
      keyword,
      document,
      scope,
      content_type: contentType,
      segment_id: segmentId
    }
  })
  return {
    total: Number(data?.total) || 0,
    totalChars: Number(data?.total_chars) || 0,
    page: Number(data?.page) || page,
    pageSize: Number(data?.page_size) || pageSize,
    documents: data?.documents || [],
    items: (data?.items || []).map(mapVectorItem)
  }
}

/**
 * 统计信息。列表改为服务端分页后，卡片上的数字一律取这里的后端汇总，不能按当页算。
 * - 普通用户：storedCount / pendingCount / rejectedCount / vectorCount / fileCount 均限本人私人库
 * - 管理员：上面几项是全库口径；public* 系列是公共库口径（审核页用），另含桶级 total_size
 */
export function getTags() {
  const num = (v) => Number(v) || 0
  return api('/knowledge/tags').then((data) => ({
    storedCount: num(data?.stored_count),
    rejectedCount: num(data?.rejected_count),
    pendingCount: num(data?.pending_count),
    vectorCount: num(data?.vector_count),
    // 本人私人库口径，与管理员的 vector_count（全库）区分开
    privateVectorCount: num(data?.private_vector_count),
    privateFileCount: num(data?.private_file_count),
    fileCount: num(data?.file_count),
    publicFileCount: num(data?.public_file_count),
    publicStoredCount: num(data?.public_stored_count),
    publicPendingCount: num(data?.public_pending_count),
    publicRejectedCount: num(data?.public_rejected_count),
    publicPendingSize: num(data?.public_pending_size),
    totalSize: num(data?.total_size),
    totalFiles: num(data?.total_files)
  }))
}
