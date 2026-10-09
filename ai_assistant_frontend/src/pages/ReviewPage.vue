<script setup>
import { ref, computed, nextTick, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  STORAGE,
  EMBEDDING,
  TYPE_META,
  REVIEW_REASONS,
  STATUS_OPTIONS,
  deleteFile,
  fetchFileBlob,
  formatSize,
  listFiles,
  previewFile,
  reviewFile,
  getTags
} from '@/api'

const router = useRouter()

const keyword = ref('')
// 后端文件状态：pending / approved / stored / rejected / failed，'all' 表示不限
const statusFilter = ref('pending')
const page = ref(1)
const pageSize = ref(10)
const total = ref(0)
const selected = ref([])
const detail = ref(null)
const drawerOpen = ref(false)
const rejectOpen = ref(false)
const rejectTarget = ref(null)
const rejectReason = ref(REVIEW_REASONS[0])
const rejectNote = ref('')
const processing = ref(false)
/** 详情抽屉里的正文预览：{ available, text, totalChars, truncated, error } */
const preview = ref(null)
const previewLoading = ref(false)
const downloading = ref(false)
const loading = ref(false)
const tableRef = ref()
const viewportWidth = ref(window.innerWidth)

/** 审核队列仅面向公共知识库（scope=public），只保留当前页 */
const docs = ref([])
/** 统计一律取后端汇总：列表已分页，不能按当前页的行去算 */
const tags = ref({
  publicPendingCount: 0, publicStoredCount: 0, publicRejectedCount: 0, publicPendingSize: 0
})

const drawerSize = computed(() =>
  viewportWidth.value < 768 ? '92%' : viewportWidth.value < 1024 ? '68%' : '48%'
)

function onResize() {
  viewportWidth.value = window.innerWidth
}

onMounted(() => {
  window.addEventListener('resize', onResize)
  load()
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  clearTimeout(timer)
})

const typeMeta = TYPE_META

/** 中文状态 -> 标签样式，保持模板写法不变 */
const statusMeta = {
  待审核: { type: 'warning' },
  待解析: { type: 'primary' },
  已入库: { type: 'success' },
  已驳回: { type: 'danger' },
  入库失败: { type: 'danger' }
}

const flow = [
  { title: '管理员上传', desc: `写入对象存储桶 ${STORAGE.bucket}` },
  { title: '元数据落库', desc: '状态置为「待审核」，进入审核队列' },
  { title: '管理员审核', desc: '核对后通过或驳回' },
  { title: '解析入库', desc: '切分向量化，全员可检索' }
]

const stats = computed(() => [
  { label: '公共库待审核', value: tags.value.publicPendingCount, unit: '份', tone: 'warning' },
  {
    label: '待审核文档体积',
    // 用 formatSize 自适应单位。原来固定按 MB 保留 1 位小数，
    // 几 KB 的待审核文档会被显示成「0.0 MB」——看着像统计坏了，其实后端算得没错。
    // formatSize 自带单位（B / KB / MB），所以 unit 留空。
    value: formatSize(tags.value.publicPendingSize),
    unit: '',
    tone: 'primary'
  },
  { label: '公共库已入库', value: tags.value.publicStoredCount, unit: '份', tone: 'success' },
  { label: '已驳回申请', value: tags.value.publicRejectedCount, unit: '份', tone: 'danger' }
])

/** 拉当前页 + 汇总统计；状态筛选和关键字搜索都由后端执行 */
async function load() {
  loading.value = true
  try {
    const [fileData, tagData] = await Promise.all([
      listFiles({
        scope: 'public',
        page: page.value,
        pageSize: pageSize.value,
        status: statusFilter.value === 'all' ? undefined : statusFilter.value,
        keyword: keyword.value.trim() || undefined
      }),
      getTags()
    ])
    docs.value = fileData.items
    total.value = fileData.total
    tags.value = tagData
    // 换页后清空跨页选中，避免把上一页勾选的文件带进批量操作
    selected.value = []

    // 抽屉打开时同步最新状态，避免审核后详情仍显示旧状态
    if (detail.value) {
      detail.value = fileData.items.find((d) => d.id === detail.value.id) || detail.value
    }

    // 审核完最后一条或筛选变化后当前页可能已越界，回退到最后一页
    const maxPage = Math.max(1, Math.ceil(fileData.total / pageSize.value))
    if (page.value > maxPage) {
      page.value = maxPage
      return load()
    }
  } catch (e) {
    ElMessage.error(e.message || '审核队列加载失败')
  } finally {
    loading.value = false
  }
}

/**
 * 统一的加载调度：条件变化回第 1 页，150ms 防抖，
 * 避免「页码归 1」和「筛选变化」在同一交互里重复发请求。
 */
let timer = null
function scheduleLoad(resetPage = false) {
  if (resetPage) page.value = 1
  clearTimeout(timer)
  timer = setTimeout(load, 150)
}

watch([keyword, statusFilter, pageSize], () => scheduleLoad(true))
watch(page, () => scheduleLoad(false))

function resetFilters() {
  keyword.value = ''
  statusFilter.value = 'pending'
  ElMessage.success('筛选条件已重置')
}

function handleSelectionChange(rows) {
  selected.value = rows
}

/** 抽屉里的上传者：姓名优先，账号相同就不重复显示 */
const uploaderLabel = computed(() => {
  const name = detail.value?.uploader || ''
  const account = detail.value?.uploaderAccount || ''
  if (!name) return '—'
  return account && account !== name ? `${name}（${account}）` : name
})

/** 抽屉里的审核人：与上传者同一套拼法，未审核时明确写出来 */
const reviewerLabel = computed(() => {
  const name = detail.value?.reviewer || ''
  const account = detail.value?.reviewerAccount || ''
  if (!name) return '待审核'
  return account && account !== name ? `${name}（${account}）` : name
})

function openDetail(row) {
  detail.value = row
  drawerOpen.value = true
  loadPreview(row)
}

/** 正文预览。待审核文件还没有分段，正文只能由后端现从对象存储取。 */
async function loadPreview(row) {
  previewLoading.value = true
  preview.value = null
  try {
    preview.value = await previewFile(row.id)
  } catch (e) {
    preview.value = {
      available: false,
      text: '',
      totalChars: 0,
      truncated: false,
      error: e.message || '正文加载失败'
    }
  } finally {
    previewLoading.value = false
  }
}

/** 下载原始文件。接口要带 Authorization 头，所以只能取成 blob 再手动触发下载 */
async function downloadOriginal() {
  if (!detail.value) return
  downloading.value = true
  try {
    const blob = await fetchFileBlob(detail.value.id)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = detail.value.name
    document.body.appendChild(link)
    link.click()
    link.remove()
    // 延后释放：立刻 revoke 有可能在下载真正开始前就把 blob 失效掉
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (e) {
    ElMessage.error(e.message || '下载失败，请稍后重试')
  } finally {
    downloading.value = false
  }
}

function clearSelection() {
  nextTick(() => tableRef.value?.clearSelection())
}

async function approve(row) {
  try {
    await ElMessageBox.confirm(
      `通过后系统将从对象存储桶 ${STORAGE.bucket} 读回「${row.name}」，并立即触发解析、切分与向量写入。`,
      '确认审核通过',
      { confirmButtonText: '通过并解析入库', cancelButtonText: '暂不处理', type: 'info' }
    )
  } catch (e) {
    return
  }
  processing.value = true
  try {
    const res = await reviewFile(row.id, { approved: true })
    // 后端审核通过后同步解析入库，失败时返回 status = failed
    if (res?.status === 'failed') {
      ElMessage.warning(`「${row.name}」审核已通过，但解析入库失败`)
    } else {
      ElMessage.success(`「${row.name}」审核通过，已完成解析并写入语料库`)
    }
    await load()
    clearSelection()
  } catch (e) {
    ElMessage.error(e.message || '审核失败')
  } finally {
    processing.value = false
  }
}

/**
 * 删除公共库文档（向量分段 + 文件记录 + 对象存储文件一起清）。
 *
 * 确认文案按状态区分：**已入库的删掉会影响所有人的检索**，必须说清楚；
 * 待审核只是撤回这次导入申请，性质完全不同，不能套同一句话。
 */
async function removeDoc(row) {
  const stored = row.rawStatus === 'stored'
  const message = stored
    ? `《${row.name}》已入库，删除后它的全部语料分段会被清除，所有用户都将无法再检索到它，且无法恢复。`
    : row.rawStatus === 'pending'
      ? `将撤回《${row.name}》的导入申请，对象存储里暂存的文件也会一并清除，无法恢复。`
      : `将删除《${row.name}》的记录，无法恢复。`

  try {
    await ElMessageBox.confirm(message, stored ? '下架公共库文档' : '删除文档', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning'
    })
  } catch (e) {
    return
  }

  processing.value = true
  try {
    const res = await deleteFile(row.id)
    ElMessage.success(res.deletedSegments
      ? `已删除「${row.name}」及其 ${res.deletedSegments} 个文本段`
      : `已删除「${row.name}」`)
    if (!res.storageRemoved) {
      // 分段与记录已清干净，只是对象存储里的原文件没删掉，如实告知
      ElMessage.warning('文档已删除，但对象存储中的原文件未能清除')
    }
    // 详情抽屉正开着这条记录的话，一并关掉，别留着已经不存在的数据
    if (detail.value?.id === row.id) drawerOpen.value = false
    await load()
  } catch (e) {
    ElMessage.error(e.message || '删除失败，请稍后重试')
  } finally {
    processing.value = false
  }
}

async function approveSelected() {
  if (!selected.value.length) {
    ElMessage.warning('请先勾选需要通过的文件')
    return
  }
  const rows = [...selected.value]
  processing.value = true
  let success = 0
  try {
    for (const row of rows) {
      try {
        await reviewFile(row.id, { approved: true })
        success += 1
      } catch (e) {
        ElMessage.error(`「${row.name}」审核失败：${e.message}`)
      }
    }
    if (success) ElMessage.success(`已通过 ${success} 份文件，解析任务已提交至语料库`)
    selected.value = []
    clearSelection()
    await load()
  } finally {
    processing.value = false
  }
}

function openReject(row) {
  rejectTarget.value = row
  rejectReason.value = REVIEW_REASONS[0]
  rejectNote.value = ''
  rejectOpen.value = true
}

async function confirmReject() {
  const row = rejectTarget.value
  if (!row) return
  const note = rejectNote.value.trim()
  const reason = note ? `${rejectReason.value}；补充说明：${note}` : rejectReason.value
  processing.value = true
  try {
    await reviewFile(row.id, { approved: false, comment: reason })
    rejectOpen.value = false
    ElMessage.success(`已驳回「${row.name}」`)
    await load()
    clearSelection()
  } catch (e) {
    ElMessage.error(e.message || '驳回失败')
  } finally {
    processing.value = false
  }
}

function goMine() {
  router.push('/knowledge/mine')
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">公共知识库导入审核</h1>
      </div>
      <div class="head-actions">
        <el-tag type="danger" effect="light" round>仅管理员 · 公共知识库</el-tag>
        <el-button type="primary" @click="goMine">前往我的知识库</el-button>
      </div>
    </div>

    <div class="stat-grid">
      <div v-for="s in stats" :key="s.label" class="stat-card" :class="`stat-card--${s.tone}`">
        <span class="stat-card__label">{{ s.label }}</span>
        <span class="stat-card__value">{{ s.value }}<em>{{ s.unit }}</em></span>
      </div>
    </div>

    <section class="panel flow-panel">
      <header class="panel__head">
        <h2>审核链路</h2>
        <span class="panel__hint">MinIO · 桶 {{ STORAGE.bucket }}</span>
      </header>
      <ol class="flow">
        <li v-for="(item, i) in flow" :key="item.title" class="flow__item">
          <span class="flow__index">{{ i + 1 }}</span>
          <div class="flow__text">
            <strong>{{ item.title }}</strong>
            <p>{{ item.desc }}</p>
          </div>
        </li>
      </ol>
    </section>

    <section class="panel filter-panel">
      <el-input
        v-model="keyword"
        class="filter-item filter-item--grow"
        placeholder="搜索文档名称、申请编号或 MinIO 对象路径"
        clearable
      >
        <template #prefix>
          <svg viewBox="0 0 24 24" width="16" height="16"><circle cx="11" cy="11" r="6.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M16 16l4.5 4.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
        </template>
      </el-input>
      <el-select v-model="statusFilter" class="filter-item filter-item--sm">
        <el-option v-for="o in STATUS_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
      </el-select>
      <el-button @click="resetFilters">重置</el-button>
    </section>

    <section class="panel table-panel">
      <header class="panel__head">
        <h2>公共知识库审核队列</h2>
        <div class="table-actions">
          <span class="panel__hint">共 {{ total }} 条记录 · 私人知识库不进入审核流程</span>
          <el-button type="primary" :disabled="!selected.length" :loading="processing" @click="approveSelected">
            批量通过{{ selected.length ? `（${selected.length}）` : '' }}
          </el-button>
        </div>
      </header>

      <el-table
        ref="tableRef"
        v-loading="loading"
        :data="docs"
        style="width: 100%"
        stripe
        @selection-change="handleSelectionChange"
      >
        <el-table-column type="selection" width="46" :selectable="(row) => row.rawStatus === 'pending'" />
        <el-table-column label="文档" min-width="250">
          <template #default="{ row }">
            <div class="doc-cell">
              <span class="file-badge" :style="{ background: typeMeta[row.type]?.color }">
                {{ typeMeta[row.type]?.label || row.type }}
              </span>
              <div class="doc-cell__text">
                <!-- 这里原来还有一行 {{ row.id }} · {{ storage.contentType }}：
                     格式左边徽标已经显示了，再列一遍原始 MIME 是冗余；
                     文件 id 对审核员也没用，还长到把单元格撑成 5 行。
                     真要看 id 到详情抽屉里找。 -->
                <span class="doc-cell__name">{{ row.name }}</span>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="size" label="大小" width="90" />
        <el-table-column label="上传者" width="150">
          <template #default="{ row }">
            <!-- 显示姓名；登录账号放 tooltip 里，遇到同名的人还能分辨 -->
            <el-tooltip :disabled="!row.uploaderAccount" :content="row.uploaderAccount" placement="top">
              <span class="uploader-name">{{ row.uploader }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="对象存储路径" min-width="220">
          <template #default="{ row }">
            <el-tooltip :content="row.storage.key" placement="top">
              <span class="obj-key">{{ row.storage.key }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="createdAt" label="提交时间" width="150" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tooltip :disabled="!row.errorMessage" :content="row.errorMessage" placement="top">
              <el-tag size="small" :type="statusMeta[row.status]?.type" effect="light">{{ row.status }}</el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.rawStatus === 'pending'" link type="primary" size="small" :disabled="processing" @click="approve(row)">通过</el-button>
            <el-button v-if="row.rawStatus === 'pending'" link type="danger" size="small" :disabled="processing" @click="openReject(row)">驳回</el-button>
            <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
            <el-button link type="danger" size="small" :disabled="processing" @click="removeDoc(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <div class="empty">
            <p>当前筛选条件下没有审核记录</p>
            <el-button link type="primary" @click="resetFilters">清除筛选条件</el-button>
          </div>
        </template>
      </el-table>

      <!-- 服务端分页：换页/换每页条数都会重新查库 -->
      <div class="table-pager">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :page-sizes="[10, 20, 50]"
          :total="total"
          layout="total, sizes, prev, pager, next"
          background
        />
      </div>
    </section>

    <el-drawer v-model="drawerOpen" :size="drawerSize" title="审核详情">
      <template v-if="detail">
        <div class="detail-head">
          <div>
            <h3>{{ detail.name }}</h3>
            <!-- 只留文档 id 作为排查用的句柄；Content-Type 在下面「文件信息」里已有 -->
            <small>{{ detail.id }}</small>
          </div>
          <el-tag size="small" :type="statusMeta[detail.status]?.type" effect="light">{{ detail.status }}</el-tag>
        </div>

        <!-- 正文放最前面：它才是审核要判断的东西，元数据只是辅助 -->
        <h4 class="detail-label">文档正文</h4>
        <div v-if="previewLoading" class="preview preview--muted">正在读取并解析文档…</div>
        <div v-else-if="preview && !preview.available" class="preview preview--error">
          <strong>无法预览正文：解析失败</strong>
          <p class="preview__error">{{ preview.error }}</p>
          <p class="preview__hint">
            解析失败的文档即使审核通过也入不了库，只会多出一条「入库失败」记录，建议直接驳回。
          </p>
        </div>
        <template v-else-if="preview">
          <div class="preview">
            <pre v-if="preview.text" class="preview__text">{{ preview.text }}</pre>
            <p v-else class="preview__hint">没有抽取到任何文字，可能是扫描件或纯图片。</p>
          </div>
          <p class="preview__meta">
            共 {{ preview.totalChars }} 字<template v-if="preview.truncated">，此处仅显示前 {{ preview.text.length }} 字</template>
            <el-button link type="primary" size="small" :loading="downloading" @click="downloadOriginal">
              下载原文
            </el-button>
          </p>
        </template>

        <h4 class="detail-label">文件信息</h4>
        <div class="meta-grid">
          <div class="meta-item"><span>文件大小</span><strong>{{ detail.size }}</strong></div>
          <div class="meta-item"><span>格式</span><strong>{{ typeMeta[detail.type]?.label || detail.type }}</strong></div>
          <div class="meta-item"><span>所属知识库</span><strong>{{ detail.kbName }}</strong></div>
          <div class="meta-item"><span>上传者</span><strong>{{ uploaderLabel }}</strong></div>
          <div class="meta-item"><span>提交时间</span><strong>{{ detail.createdAt }}</strong></div>
          <!-- 只有入库失败的文件才有这一项，正常文件不显示空占位 -->
          <div v-if="detail.rawStatus === 'failed'" class="meta-item meta-item--wide">
            <span>入库失败原因</span><strong>{{ detail.errorMessage || '—' }}</strong>
          </div>
        </div>

        <h4 class="detail-label">对象存储信息</h4>
        <div class="meta-grid">
          <div class="meta-item"><span>存储桶</span><strong>{{ detail.storage.bucket }}</strong></div>
          <div class="meta-item meta-item--wide"><span>对象路径</span><strong>{{ detail.storage.key }}</strong></div>
          <div class="meta-item"><span>上传时间</span><strong>{{ detail.storage.uploadedAt }}</strong></div>
        </div>

        <h4 class="detail-label">审核记录</h4>
        <div class="meta-grid">
          <div class="meta-item"><span>审核人</span><strong>{{ reviewerLabel }}</strong></div>
          <div class="meta-item"><span>审核时间</span><strong>{{ detail.reviewedAt || '—' }}</strong></div>
          <div class="meta-item meta-item--wide"><span>审核意见</span><strong>{{ detail.remark || '暂无意见' }}</strong></div>
        </div>

        <h4 class="detail-label">入库结果</h4>
        <div class="meta-grid">
          <div class="meta-item"><span>入库状态</span><strong>{{ detail.status }}</strong></div>
          <div class="meta-item"><span>入库时间</span><strong>{{ detail.storage.accessedAt || '尚未入库' }}</strong></div>
          <div class="meta-item"><span>向量模型</span><strong>{{ EMBEDDING.model }}</strong></div>
        </div>

        <div class="detail-actions">
          <el-button v-if="detail.rawStatus === 'pending'" type="primary" :loading="processing" @click="approve(detail)">通过并解析入库</el-button>
          <el-button v-if="detail.rawStatus === 'pending'" type="danger" plain :disabled="processing" @click="openReject(detail)">驳回申请</el-button>
        </div>
      </template>
    </el-drawer>

    <el-dialog v-model="rejectOpen" title="驳回导入申请" width="min(520px, 92vw)">
      <p class="dialog-tip">驳回不触发解析，文件保留在暂存桶，提交人可修改后重新提交。</p>
      <p class="dialog-label">驳回原因</p>
      <div class="reason-list">
        <button
          v-for="r in REVIEW_REASONS"
          :key="r"
          class="reason-item"
          :class="{ 'is-active': rejectReason === r }"
          @click="rejectReason = r"
        >
          <span class="reason-dot"></span>
          {{ r }}
        </button>
      </div>
      <p class="dialog-label">补充说明（选填）</p>
      <el-input v-model="rejectNote" type="textarea" :rows="3" maxlength="120" show-word-limit placeholder="可补充需要提交人调整的具体章节或字段" />
      <template #footer>
        <el-button @click="rejectOpen = false">取消</el-button>
        <el-button type="danger" :loading="processing" @click="confirmReject">确认驳回</el-button>
      </template>
    </el-dialog>
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
  grid-template-columns: repeat(4, minmax(0, 1fr));
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
.stat-card--success { border-left-color: var(--color-success); }
.stat-card--warning { border-left-color: var(--color-warning); }
.stat-card--danger { border-left-color: var(--color-danger); }

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

.flow-panel,
.filter-panel,
.table-panel {
  padding: var(--space-5);
}

.flow-panel {
  margin-bottom: var(--space-4);
}

.filter-panel {
  display: flex;
  gap: var(--space-3);
  padding: var(--space-4);
  margin-bottom: var(--space-4);
  flex-wrap: wrap;
}

.table-panel {
  padding: var(--space-4) var(--space-4) var(--space-3);
  overflow: hidden;
}

.table-pager {
  display: flex;
  justify-content: flex-end;
  padding-top: var(--space-4);
}

.panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
  flex-wrap: wrap;
}

.panel__head h2 {
  font-size: 16px;
}

.panel__hint {
  font-size: 12px;
  color: var(--color-text-muted);
  word-break: break-all;
}

.table-actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.flow {
  list-style: none;
  padding: 0;
  margin: 0;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-4);
}

.flow__item {
  display: flex;
  gap: var(--space-3);
  background: var(--color-surface-alt);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: var(--space-3);
  transition: all var(--transition);
}

.flow__item:hover {
  border-color: var(--color-primary-light);
  background: var(--color-primary-soft);
}

.flow__index {
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--color-primary);
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.flow__text strong {
  font-size: 13px;
}

.flow__text p {
  margin-top: 2px;
  font-size: 12px;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.filter-item--grow {
  flex: 1;
  min-width: 220px;
}

.filter-item--sm {
  width: 150px;
}

.doc-cell {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.file-badge {
  font-size: 11px;
  color: #fff;
  border-radius: var(--radius-sm);
  padding: 2px 7px;
  flex-shrink: 0;
}

.doc-cell__text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.doc-cell__name {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-cell__text small {
  font-size: 11px;
  color: var(--color-text-muted);
}

.obj-key {
  display: inline-block;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--color-text-secondary);
  background: var(--color-surface-alt);
  border-radius: var(--radius-sm);
  padding: 2px 8px;
  vertical-align: middle;
}

/* 上传者姓名。不要用 .obj-key —— 那是给对象路径用的等宽小字，中文姓名挤在里面很难读 */
.uploader-name {
  display: inline-block;
  max-width: 130px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}

.empty {
  padding: var(--space-6) 0;
  color: var(--color-text-muted);
  font-size: 13px;
}

.empty p {
  margin-bottom: var(--space-2);
}

.detail-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
}

.detail-head h3 {
  font-size: 16px;
  word-break: break-all;
}

.detail-head small {
  font-size: 12px;
  color: var(--color-text-muted);
}

.detail-label {
  margin: var(--space-5) 0 var(--space-2);
  font-size: 14px;
}

/* ---- 详情抽屉里的正文预览 ---- */

.preview {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-alt);
  padding: var(--space-3) var(--space-4);
  /* 长文档要能滚，不能把抽屉撑到天上 */
  max-height: 340px;
  overflow: auto;
}

.preview--muted {
  font-size: 13px;
  color: var(--color-text-muted);
}

.preview--error {
  border-color: var(--color-danger);
  font-size: 13px;
}

.preview--error strong {
  color: var(--color-danger);
}

.preview__error {
  margin: var(--space-2) 0 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  line-height: 1.6;
  color: var(--color-text-secondary);
  word-break: break-all;
}

.preview__hint {
  margin: var(--space-2) 0 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--color-text-muted);
}

/* 保留原文换行，但用正文字体 —— 中文用等宽字体很难读 */
.preview__text {
  margin: 0;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.8;
  color: var(--color-text);
  white-space: pre-wrap;
  word-break: break-word;
}

.preview__meta {
  margin: var(--space-2) 0 0;
  font-size: 12px;
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.meta-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-3);
  background: var(--color-surface-alt);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--space-4);
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.meta-item--wide {
  grid-column: 1 / -1;
}

.meta-item span {
  font-size: 11px;
  color: var(--color-text-muted);
}

.meta-item strong {
  font-size: 13px;
  font-weight: 600;
  word-break: break-all;
}

.detail-actions {
  display: flex;
  gap: var(--space-3);
  margin-top: var(--space-5);
  flex-wrap: wrap;
}

.dialog-tip {
  font-size: 13px;
  color: var(--color-text-secondary);
  line-height: 1.7;
  background: var(--color-primary-soft);
  border-radius: var(--radius-sm);
  padding: var(--space-3);
}

.dialog-label {
  margin: var(--space-4) 0 var(--space-2);
  font-size: 13px;
  font-weight: 600;
}

.reason-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.reason-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  text-align: left;
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
  font-family: inherit;
  font-size: 13px;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all var(--transition);
}

.reason-item:hover {
  border-color: var(--color-primary-light);
}

.reason-item.is-active {
  border-color: var(--color-primary);
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-weight: 600;
}

.reason-dot {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  border: 1.5px solid var(--color-border-strong);
  flex-shrink: 0;
}

.reason-item.is-active .reason-dot {
  border-color: var(--color-primary);
  box-shadow: inset 0 0 0 3px var(--color-primary);
}

@media (max-width: 1023px) {
  .stat-grid,
  .flow {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .filter-item--grow {
    flex-basis: 100%;
  }
}

@media (max-width: 767px) {
  .stat-grid,
  .flow {
    grid-template-columns: 1fr;
  }

  .flow-panel,
  .table-panel {
    padding: var(--space-4);
  }

  .filter-panel {
    padding: var(--space-3);
  }

  .filter-item--sm {
    width: calc(50% - var(--space-2));
  }

  .meta-grid {
    grid-template-columns: 1fr;
  }

  .obj-key {
    max-width: 150px;
  }
}

@media (max-width: 480px) {
  .filter-item--sm {
    width: 100%;
  }

  .table-actions {
    width: 100%;
    justify-content: space-between;
  }
}
</style>
