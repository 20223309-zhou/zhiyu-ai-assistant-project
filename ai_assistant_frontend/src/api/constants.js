/**
 * 前端常量。这里的取值需要与后端保持一致，后端尚未提供配置下发接口，
 * 因此暂时在前端硬编码，后续建议改为由后端返回。
 *
 * 对应后端：
 *  - 存储桶 / MinIO：config/minio/minio_client.py
 *  - 向量模型 / 维度：config/pgsql/pgvector_client.py
 *  - 单文件上限、允许类型：knowledge.py
 */

/** MinIO / 对象存储桶名（后端 minio_client.py 中 BUCKET = "knowledge"） */
export const STORAGE = {
  bucket: 'knowledge'
}

/** 向量模型信息（后端 pgvector_client.py 中 embedding_length = 1024） */
export const EMBEDDING = {
  model: 'qwen3-embedding:0.6b',
  dim: 1024
}

/**
 * 上传限制（后端 knowledge.py 中 MAX_SIZE = 50MB，前端按产品要求收紧到 20MB）
 * accept 必须与后端 ALLOWED_TYPES 对得上：后端按 content_type 校验，
 * 老版 Word（.doc → application/msword）不在白名单里，列出来只会误导用户
 * ——文件能选中、却在服务端被 400 拒绝。
 */
export const UPLOAD = {
  maxSize: 20 * 1024 * 1024,
  accept: ['.docx', '.pdf', '.txt', '.md'],
  /** 前端后缀 -> MIME，用于展示；后端以 content_type 为准做校验 */
  contentType: {
    pdf: 'application/pdf',
    doc: 'application/msword',
    docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    txt: 'text/plain',
    md: 'text/markdown'
  }
}

/** 文件类型徽标 */
export const TYPE_META = {
  pdf: { label: 'PDF', color: '#f0564f' },
  docx: { label: 'Word', color: '#3a63db' },
  doc: { label: 'Word', color: '#3a63db' },
  txt: { label: 'TXT', color: '#5b6779' },
  md: { label: 'Markdown', color: '#16b981' }
}

/** 后端文件状态 -> 中文文案（后端 knowledge_model.py 的 CheckConstraint） */
export const STATUS_TEXT = {
  pending: '待审核',
  approved: '待解析',
  stored: '已入库',
  rejected: '已驳回',
  failed: '入库失败'
}

/** 状态筛选项（供审核页下拉使用） */
export const STATUS_OPTIONS = [
  { label: '全部状态', value: 'all' },
  { label: '待审核', value: 'pending' },
  { label: '已入库', value: 'stored' },
  { label: '已驳回', value: 'rejected' },
  { label: '入库失败', value: 'failed' }
]

/** 对话页推荐问题（纯 UI 文案，非接口数据） */
export const SUGGESTIONS = [
  '知识库支持导入哪些格式的文档？',
  '语料是怎么切分的？元数据包含哪些字段？',
  '普通用户和管理员的权限有什么区别？',
  '检索置信度低的时候系统会怎么处理？'
]

/** 驳回原因模板（纯 UI 文案） */
export const REVIEW_REASONS = [
  '文件含敏感信息，需脱敏后重新提交',
  '文档版本过旧，与现行制度存在冲突',
  '内容与知识库已有文档高度重复',
  '扫描件排版混乱，正文抽取质量偏低',
  '非本部门归档范围，请转交对应部门'
]

/** 知识库固定名称（后端 register / lifespan 中创建） */
export const PUBLIC_KB_NAME = '公共知识库'
export const PRIVATE_KB_NAME = '我的知识库'
