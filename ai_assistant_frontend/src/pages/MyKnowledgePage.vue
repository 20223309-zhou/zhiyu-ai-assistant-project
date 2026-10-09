<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import {
  UPLOAD,
  TYPE_META,
  PUBLIC_KB_NAME,
  PRIVATE_KB_NAME,
  checkFileConflict,
  deleteFile,
  formatSize,
  listFiles,
  uploadPrivateFile,
  uploadPublicFile,
  getTags
} from '@/api'
import { useUserStore } from '@/utils/userStore'

const router = useRouter()
const { isAdmin, displayName } = useUserStore()

const ACCEPT = UPLOAD.accept
const MAX_SIZE = UPLOAD.maxSize
const typeMeta = TYPE_META

/** 私人文档表格：服务端分页，只保留当前页 */
const docs = ref([])
const docPage = ref(1)
const docPageSize = ref(10)
const docTotal = ref(0)
const loading = ref(false)

/**
 * 统计数字全部取自后端汇总。表格已分页，不能再按当前页的行去算。
 * 管理员用 public* 系列（公共库口径），普通用户用本人的私人库口径。
 */
const tags = ref({
  storedCount: 0, rejectedCount: 0, pendingCount: 0, vectorCount: 0,
  privateVectorCount: 0, privateFileCount: 0,
  fileCount: 0, publicStoredCount: 0, publicPendingCount: 0, publicPendingSize: 0
})

/**
 * 私人知识库由后端在注册时自动创建（main.py -> POST /users/register），
 * 且后端没有「知识库列表」接口，因此这里只展示名称与创建者，不提供创建入口。
 */
const myKb = computed(() => ({
  name: PRIVATE_KB_NAME,
  ownerName: displayName.value
}))

const privatePending = ref([])
const publicPending = ref([])
const privateUploading = ref(false)
const publicUploading = ref(false)
const privateUploadRef = ref()
const publicUploadRef = ref()

// 四张卡的数字全部来自 /knowledge/tags。管理员的 vectorCount 是全库口径，
// 私人库相关的数字一律用 private* 系列，避免同一屏出现两个互相矛盾的值。
const stats = computed(() => {
  // 本人私人库。两种角色都有，所以第一张卡统一是它（不再放写死的「1 / 1 个」）
  const myDocs = { label: '我的私人文档', value: tags.value.privateFileCount, unit: '份', tone: 'primary' }
  if (!isAdmin.value) {
    return [
      myDocs,
      { label: '我的私人语料', value: tags.value.privateVectorCount, unit: '段', tone: 'accent' },
      { label: '我的已入库文档', value: tags.value.storedCount, unit: '份', tone: 'success' }
    ]
  }
  return [
    myDocs,
    { label: '全库语料分段', value: tags.value.vectorCount, unit: '段', tone: 'accent' },
    { label: '公共知识库已入库', value: tags.value.publicStoredCount, unit: '份', tone: 'success' },
    { label: '公共库待审核', value: tags.value.publicPendingCount, unit: '份', tone: 'warning' }
  ]
})

function extOf(name) {
  const idx = name.lastIndexOf('.')
  return idx === -1 ? '' : name.slice(idx).toLowerCase()
}

/** 拉取当前页私人文档 + 汇总统计（两个请求并行） */
async function refresh() {
  loading.value = true
  try {
    const [fileData, tagData] = await Promise.all([
      listFiles({ scope: 'private', page: docPage.value, pageSize: docPageSize.value }),
      getTags()
    ])
    docs.value = fileData.items
    docTotal.value = fileData.total
    tags.value = tagData
    // 删除/筛选后当前页可能已越界，回退到最后一页
    const maxPage = Math.max(1, Math.ceil(fileData.total / docPageSize.value))
    if (docPage.value > maxPage) {
      docPage.value = maxPage
    }
  } catch (e) {
    ElMessage.error(e.message || '知识库数据加载失败')
  } finally {
    loading.value = false
  }
}

/**
 * 加载调度：换每页条数回第 1 页，150ms 防抖，
 * 避免「页码归 1」和「条数变化」在同一交互里重复发请求。
 */
let timer = null
function scheduleLoad(resetPage = false) {
  if (resetPage) docPage.value = 1
  clearTimeout(timer)
  timer = setTimeout(refresh, 150)
}

watch(docPageSize, () => scheduleLoad(true))
watch(docPage, () => scheduleLoad(false))
onMounted(refresh)
onBeforeUnmount(() => clearTimeout(timer))

function addFile(file, list) {
  const ext = extOf(file.name)
  if (!ACCEPT.includes(ext)) {
    ElMessage.error(`「${file.name}」格式不支持，仅可上传 Word / PDF / TXT / Markdown`)
    return
  }
  if (file.size > MAX_SIZE) {
    ElMessage.error(`「${file.name}」超过 20MB 限制`)
    return
  }
  if (list.value.some((f) => f.name === file.name && f.size === file.size)) {
    ElMessage.warning(`「${file.name}」已在待上传列表中`)
    return
  }
  list.value.push({
    uid: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    raw: file,
    name: file.name,
    size: file.size,
    sizeBytes: file.size,
    sizeText: formatSize(file.size),
    ext: ext.slice(1),
    status: 'waiting',
    error: ''
  })
}

function handlePrivateChange(file) {
  addFile(file.raw || file, privatePending)
  privateUploadRef.value?.clearFiles()
}

function handlePublicChange(file) {
  addFile(file.raw || file, publicPending)
  publicUploadRef.value?.clearFiles()
}

function removePending(list, uid) {
  list.value = list.value.filter((f) => f.uid !== uid)
}

function removePrivate(uid) {
  removePending(privatePending, uid)
}

/**
 * 同名冲突确认。返回 'replace'（继续上传并替换）或 'cancel'（跳过该文件）。
 *
 * 替换是破坏性操作，不能让用户不知情 —— 上传前必须先探测再问。
 * 这里只覆盖私人库：公共库有审核流程，替换会绕开审核，一律不做自动替换。
 */
async function confirmReplace(name) {
  let info
  try {
    info = await checkFileConflict(name)
  } catch (e) {
    // 探测失败不该把上传整个挡住，按「无冲突」继续
    return 'replace'
  }
  if (!info.conflict) return 'replace'

  const stored = info.files.some((f) => f.status === 'stored')
  try {
    await ElMessageBox.confirm(
      `知识库中已存在同名文档《${name}》${stored ? '（已入库）' : ''}。继续上传将替换它，旧文档的语料分段会被清除。`,
      '已存在同名文档',
      { confirmButtonText: '替换', cancelButtonText: '跳过此文件', type: 'warning' }
    )
    return 'replace'
  } catch (e) {
    return 'cancel' // 取消或关闭弹窗都按跳过处理
  }
}

/** 删除一份文档：向量分段、文件记录、对象存储里的原文件会一起清掉，不可恢复 */
async function removeDoc(row) {
  try {
    await ElMessageBox.confirm(
      `《${row.name}》将被永久删除，它在向量库中的语料分段也会一并清除，无法恢复。`,
      '删除文档',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
  } catch (e) {
    return
  }

  try {
    const res = await deleteFile(row.id)
    ElMessage.success(res.deletedSegments
      ? `已删除文档及其 ${res.deletedSegments} 个文本段`
      : '已删除文档')
    if (!res.storageRemoved) {
      // 分段与记录已删干净，只是对象存储里的原文件没清掉，如实告知
      ElMessage.warning('文档已删除，但对象存储中的原文件未能清除')
    }
  } catch (e) {
    ElMessage.error(e.message || '删除失败，请稍后重试')
    return
  }
  await refresh()
}

function removePublic(uid) {
  removePending(publicPending, uid)
}

function goReview() {
  router.push('/knowledge/review')
}

function goCorpus() {
  router.push('/knowledge/corpus')
}

async function runUpload(list, flag, mode) {
  if (!list.value.length) {
    ElMessage.warning('请先选择需要导入的文档')
    return
  }
  flag.value = true
  let success = 0
  try {
    for (const item of list.value) {
      item.status = 'uploading'

      // 私人库上传前先探同名：不静默覆盖，交给用户确认（后端也会兜底返回 409）
      let replace = false
      if (mode === 'private') {
        const decision = await confirmReplace(item.name)
        if (decision === 'cancel') {
          item.status = 'waiting'
          continue
        }
        replace = decision === 'replace'
      }

      try {
        await (mode === 'private'
          ? uploadPrivateFile(item.raw, { replace })
          : uploadPublicFile(item.raw))
        item.status = 'done'
        success += 1
      } catch (e) {
        item.status = 'failed'
        item.error = e.message || '上传失败'
        ElMessage.error(`「${item.name}」上传失败：${item.error}`)
      }
    }
    // 成功的从待上传列表移除，失败的保留便于重试
    list.value = list.value.filter((f) => f.status !== 'done')
    if (success) {
      ElMessage.success(
        mode === 'private'
          ? `已上传 ${success} 份文件，系统已完成解析并写入向量库`
          : `已提交 ${success} 份文件至公共知识库，审核通过后自动解析入库`
      )
    }
    await refresh()
  } finally {
    flag.value = false
  }
}

function uploadPrivate() {
  return runUpload(privatePending, privateUploading, 'private')
}

function uploadPublic() {
  return runUpload(publicPending, publicUploading, 'public')
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">我的知识库</h1>
      </div>
      <div class="head-actions">
        <el-tag :type="isAdmin ? 'danger' : 'primary'" effect="light" round>
          {{ isAdmin ? '管理员 · 可上传公共知识库' : '普通用户 · 私人知识库可用' }}
        </el-tag>
        <el-button type="primary" plain @click="goCorpus">查看语料</el-button>
      </div>
    </div>

    <div class="stat-grid">
      <div v-for="s in stats" :key="s.label" class="stat-card" :class="`stat-card--${s.tone}`">
        <span class="stat-card__label">{{ s.label }}</span>
        <span class="stat-card__value">{{ s.value }}<em>{{ s.unit }}</em></span>
      </div>
    </div>

    <div class="kb-grid">
      <section class="panel kb-panel">
        <header class="panel__head">
          <div>
            <h2>我的私人知识库</h2>
            <span class="panel__hint">每人限 1 个 · 上传即解析入库 · 语料仅本人可见</span>
          </div>
          <el-tag type="success" effect="light" round size="small">已创建</el-tag>
        </header>

        <div class="kb-card">
          <div class="kb-card__top">
            <span class="kb-card__name">{{ myKb.name }}</span>
            <el-tag size="small" type="success" effect="plain">私人 · 仅本人可见</el-tag>
          </div>
          <p class="kb-card__desc">个人资料，上传后直接入库，仅本人可检索。</p>
          <div class="kb-card__meta">
            <span>创建者：{{ myKb.ownerName }}</span>
            <!-- 用 docTotal 而不是 docs.length：表格是服务端分页的，
                 docs 只有当前页，超过一页时数量会少算 -->
            <span>文档：{{ docTotal }} 份</span>
            <!-- 私人库口径：管理员的 tags.vectorCount 是全库，不能拿来当私人库的数 -->
            <span>语料：{{ tags.privateVectorCount }} 段</span>
          </div>
        </div>

        <el-upload
          ref="privateUploadRef"
          class="uploader"
          drag
          multiple
          :auto-upload="false"
          :show-file-list="false"
          :accept="ACCEPT.join(',')"
          :on-change="handlePrivateChange"
        >
          <div class="uploader__inner">
            <svg viewBox="0 0 24 24" width="38" height="38" class="uploader__icon">
              <path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
              <path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
            </svg>
            <p class="uploader__title">拖拽文件到此处，或<em>点击选择文件</em></p>
            <p class="uploader__desc">支持 .docx / .pdf / .txt / .md，单个不超过 20MB</p>
          </div>
        </el-upload>

        <div v-if="privatePending.length" class="pending">
          <p class="pending__title">待入库文件（{{ privatePending.length }}）</p>
          <div v-for="item in privatePending" :key="item.uid" class="pending__item">
            <div class="pending__info">
              <span class="file-badge" :style="{ background: typeMeta[item.ext]?.color || '#5b6779' }">
                {{ typeMeta[item.ext]?.label || item.ext }}
              </span>
              <span class="pending__name">{{ item.name }}</span>
              <span class="pending__size">{{ item.sizeText }}</span>
              <span v-if="item.status === 'uploading'" class="pending__status">上传解析中…</span>
              <span v-if="item.status === 'failed'" class="pending__error">{{ item.error }}</span>
              <button class="pending__remove" :disabled="privateUploading" @click="removePrivate(item.uid)">移除</button>
            </div>
          </div>
        </div>

        <footer class="upload-actions">
          <el-button
            type="primary"
            size="large"
            :loading="privateUploading"
            @click="uploadPrivate"
          >
            {{ privateUploading ? '正在解析并写入向量库…' : '上传并直接入库' }}
          </el-button>
          <el-button size="large" :disabled="privateUploading || !privatePending.length" @click="privatePending = []">
            清空列表
          </el-button>
        </footer>

        <el-table v-loading="loading" :data="docs" class="doc-table" style="width: 100%" stripe>
          <el-table-column prop="name" label="文档名称" min-width="280" show-overflow-tooltip />
          <el-table-column label="格式" width="110">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :style="{ color: typeMeta[row.type]?.color, borderColor: typeMeta[row.type]?.color }">
                {{ typeMeta[row.type]?.label || row.type }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="size" label="大小" width="100" />
          <el-table-column prop="createdAt" label="上传时间" width="170" />
          <el-table-column label="状态" width="120">
            <template #default="{ row }">
              <el-tooltip :disabled="!row.errorMessage" :content="row.errorMessage" placement="top">
                <el-tag size="small" :type="row.rawStatus === 'stored' ? 'success' : 'danger'" effect="light">
                  {{ row.status }}
                </el-tag>
              </el-tooltip>
            </template>
          </el-table-column>
          <!-- 这里的文档都属于当前用户自己的私人库，本人可删（公共库的删除走审核页） -->
          <el-table-column label="操作" width="90">
            <template #default="{ row }">
              <el-button link type="danger" size="small" @click="removeDoc(row)">删除</el-button>
            </template>
          </el-table-column>
            <template #empty>
              <div class="empty">还没有上传文件</div>
            </template>
          </el-table>

          <!-- 服务端分页：换页/换每页条数都会重新查库 -->
          <div class="doc-pager">
            <el-pagination
              v-model:current-page="docPage"
              v-model:page-size="docPageSize"
              :page-sizes="[10, 20, 50]"
              :total="docTotal"
              layout="total, sizes, prev, pager, next"
              background
            />
          </div>
      </section>

      <section class="panel kb-panel public-panel">
        <header class="panel__head">
          <div>
            <h2>公共知识库</h2>
            <span class="panel__hint">仅管理员可上传</span>
          </div>
          <el-tag type="warning" effect="light" round size="small">共享</el-tag>
        </header>

        <div class="kb-card">
          <div class="kb-card__top">
            <span class="kb-card__name">{{ PUBLIC_KB_NAME }}</span>
            <el-tag size="small" type="warning" effect="plain">公共 · 全员可见</el-tag>
          </div>
          <p class="kb-card__desc">面向全员共享，需审核通过后入库。</p>
          <div class="kb-card__meta">
            <template v-if="isAdmin">
              <span>已入库：{{ tags.publicStoredCount }} 份</span>
              <span>待审核：{{ tags.publicPendingCount }} 份</span>
            </template>
            <span v-else>文档明细仅管理员可见</span>
          </div>
        </div>

        <template v-if="isAdmin">
          <el-upload
            ref="publicUploadRef"
            class="uploader uploader--compact"
            drag
            multiple
            :auto-upload="false"
            :show-file-list="false"
            :accept="ACCEPT.join(',')"
            :on-change="handlePublicChange"
          >
            <div class="uploader__inner">
              <svg viewBox="0 0 24 24" width="32" height="32" class="uploader__icon">
                <path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
                <path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
              </svg>
              <p class="uploader__title">上传至公共知识库</p>
              <p class="uploader__desc">提交后进入待审核队列</p>
            </div>
          </el-upload>

          <div v-if="publicPending.length" class="pending">
            <p class="pending__title">待提交审核（{{ publicPending.length }}）</p>
            <div v-for="item in publicPending" :key="item.uid" class="pending__item">
              <div class="pending__info">
                <span class="file-badge" :style="{ background: typeMeta[item.ext]?.color || '#5b6779' }">
                  {{ typeMeta[item.ext]?.label || item.ext }}
                </span>
                <span class="pending__name">{{ item.name }}</span>
                <span class="pending__size">{{ item.sizeText }}</span>
                <span v-if="item.status === 'uploading'" class="pending__status">上传中…</span>
                <span v-if="item.status === 'failed'" class="pending__error">{{ item.error }}</span>
                <button class="pending__remove" :disabled="publicUploading" @click="removePublic(item.uid)">移除</button>
              </div>
            </div>
          </div>

          <footer class="upload-actions">
            <el-button
              type="primary"
              :loading="publicUploading"
              @click="uploadPublic"
            >
              {{ publicUploading ? '正在上传至 MinIO…' : '提交至公共知识库' }}
            </el-button>
            <el-button :disabled="publicUploading || !publicPending.length" @click="publicPending = []">清空</el-button>
            <!-- 措辞要点明这是公共库文档的管理入口，不只是审核队列：
                 已入库文档的下架也在那个页面（切「已入库」筛选后操作列有删除） -->
            <el-button link type="primary" @click="goReview">管理公共库文档</el-button>
          </footer>
        </template>

        <div v-else class="readonly-box">
          <svg viewBox="0 0 24 24" width="20" height="20">
            <path d="M12 9v5m0 3.5h.01" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
            <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.8" />
          </svg>
          <span>公共知识库由管理员维护，你可检索但不可修改。</span>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.head-actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.stat-grid {
  display: grid;
  /* auto-fit：管理员 4 张、普通用户 3 张，都能均匀铺满而不会留空格 */
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--space-4);
  margin-bottom: var(--space-5);
}

.stat-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-left: 4px solid var(--color-primary);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: var(--space-4) var(--space-5);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  transition: all var(--transition);
}

.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}

.stat-card--primary { border-left-color: var(--color-primary); }
.stat-card--accent { border-left-color: var(--color-accent); }
.stat-card--success { border-left-color: var(--color-success); }
.stat-card--warning { border-left-color: var(--color-warning); }

.stat-card__label {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.stat-card__value {
  font-size: 26px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

.stat-card__value em {
  font-size: 13px;
  font-style: normal;
  font-weight: 500;
  color: var(--color-text-muted);
  margin-left: 4px;
}

/* 私人知识库独占整宽、公共知识库整块放下方。
   原来是 1.55fr : 1fr 两列，但私人库里的文档表有 7 列、声明宽度合计 814px，
   分到的 684px 装不下 —— 「上传时间」被挤没、名称被截断。
   公共库那列内容量又小，下面留一大片空白。改成上下堆叠后表格能拿到约 1180px。 */
.kb-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: var(--space-5);
}

.kb-panel {
  padding: var(--space-5);
}

.panel__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}

.panel__head h2 {
  font-size: 16px;
}

.panel__hint {
  display: block;
  margin-top: 2px;
  font-size: 12px;
  color: var(--color-text-muted);
}

.kb-card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: linear-gradient(140deg, #ffffff, #f6f9ff);
  padding: var(--space-4);
  margin-bottom: var(--space-4);
}

.kb-card__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.kb-card__name {
  font-size: 15px;
  font-weight: 600;
}

.kb-card__desc {
  margin: var(--space-2) 0 var(--space-3);
  font-size: 12px;
  line-height: 1.8;
  color: var(--color-text-secondary);
}

.kb-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-4);
  font-size: 12px;
  color: var(--color-text-secondary);
}

.uploader :deep(.el-upload) {
  width: 100%;
}

.uploader :deep(.el-upload-dragger) {
  padding: 0;
  border: 2px dashed var(--color-border-strong);
  border-radius: var(--radius-md);
  background: var(--color-surface-alt);
  transition: all var(--transition);
}

.uploader :deep(.el-upload-dragger:hover) {
  border-color: var(--color-primary);
  background: var(--color-primary-soft);
}

/* 全宽下拖拽区是一整条横向区域，竖排会把页面白白拉高一大截 */
.uploader__inner {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  text-align: left;
}

.uploader--compact .uploader__inner {
  padding: var(--space-3);
}

.uploader__icon {
  color: var(--color-primary);
  flex-shrink: 0;
}

.uploader__title {
  font-size: 14px;
  color: var(--color-text);
}

.uploader__title em {
  font-style: normal;
  color: var(--color-primary);
  font-weight: 600;
}

/* 格式说明推到右端，与左侧的提示形成一条完整的拖拽带 */
.uploader__desc {
  margin-left: auto;
  font-size: 12px;
  color: var(--color-text-muted);
  white-space: nowrap;
}

.pending {
  margin-top: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.pending__title {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.pending__item {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: var(--space-3);
  background: var(--color-surface-alt);
}

.pending__info {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
  flex-wrap: wrap;
}

.file-badge {
  font-size: 11px;
  color: #fff;
  border-radius: var(--radius-sm);
  padding: 2px 8px;
  flex-shrink: 0;
}

.pending__name {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pending__size {
  font-size: 12px;
  color: var(--color-text-muted);
}

.pending__done {
  font-size: 12px;
  color: var(--color-success);
}

.pending__status {
  font-size: 12px;
  color: var(--color-primary);
}

.pending__error {
  font-size: 12px;
  color: var(--color-danger);
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pending__remove {
  border: none;
  background: transparent;
  color: var(--color-danger);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
}

.upload-actions {
  display: flex;
  gap: var(--space-2);
  margin-top: var(--space-4);
  flex-wrap: wrap;
  align-items: center;
}

.doc-table {
  margin-top: var(--space-5);
}

.doc-pager {
  display: flex;
  justify-content: flex-end;
  padding-top: var(--space-4);
}

.empty {
  padding: var(--space-5) 0;
  font-size: 13px;
  color: var(--color-text-muted);
}

.readonly-box {
  display: flex;
  gap: var(--space-2);
  align-items: flex-start;
  background: var(--color-primary-soft);
  border: 1px solid #cdd9ff;
  border-radius: var(--radius-sm);
  padding: var(--space-3);
  font-size: 12px;
  line-height: 1.8;
  color: var(--color-primary-dark);
}

.readonly-box svg {
  flex-shrink: 0;
  margin-top: 2px;
  color: var(--color-primary);
}

@media (max-width: 1023px) {
  .stat-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 767px) {
  .kb-panel {
    padding: var(--space-4);
  }

  .stat-card {
    padding: var(--space-3) var(--space-4);
  }

  /* 窄屏放不下一条横向拖拽区，回到竖排居中 */
  .uploader__inner {
    flex-direction: column;
    text-align: center;
  }

  .uploader__desc {
    margin-left: 0;
    white-space: normal;
  }

  .upload-actions :deep(.el-button) {
    flex: 1;
    margin-left: 0;
  }
}

@media (max-width: 480px) {
  .stat-grid {
    grid-template-columns: 1fr;
  }

  .stat-card__value {
    font-size: 22px;
  }
}
</style>
