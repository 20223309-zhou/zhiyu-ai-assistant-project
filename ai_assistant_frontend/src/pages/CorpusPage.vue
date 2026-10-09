<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { TYPE_META, listVectors } from '@/api'
import { useUserStore } from '@/utils/userStore'

const router = useRouter()
const { isAdmin } = useUserStore()

const keyword = ref('')
// 知识库筛选映射到后端的 scope 参数（all / public / private）。
// 普通用户只有私人库可看，直接锁死在 private —— 后端也会把非管理员的 scope 收成 private，
// 这里同步是为了让界面上显示的筛选值和实际查询口径一致。
const kbFilter = ref(isAdmin.value ? 'all' : 'private')
// 来源文档筛选，值为文档名；可选项由后端随列表返回
const docFilter = ref('all')
const page = ref(1)
const pageSize = ref(8)
const detail = ref(null)
const drawerOpen = ref(false)
const loading = ref(false)
const viewportWidth = ref(window.innerWidth)

/** 当前页数据（服务端只返回这一页） */
const items = ref([])
const total = ref(0)
const totalChars = ref(0)
const documents = ref([])

const drawerSize = computed(() => (viewportWidth.value < 768 ? '92%' : viewportWidth.value < 1024 ? '68%' : '46%'))

function onResize() {
  viewportWidth.value = window.innerWidth
}

/**
 * 拉取当前页。分页、搜索、筛选都由后端执行，前端只负责传参和渲染。
 */
async function load() {
  loading.value = true
  try {
    const data = await listVectors({
      page: page.value,
      pageSize: pageSize.value,
      keyword: keyword.value.trim() || undefined,
      document: docFilter.value === 'all' ? undefined : docFilter.value,
      scope: kbFilter.value === 'all' ? undefined : kbFilter.value
    })
    items.value = data.items
    total.value = data.total
    totalChars.value = data.totalChars
    documents.value = data.documents

    // 筛选后当前页可能已越界（例如原本在第 5 页，筛选后只剩 2 页），回退到最后一页
    const maxPage = Math.max(1, Math.ceil(data.total / pageSize.value))
    if (page.value > maxPage) {
      page.value = maxPage
      return load()
    }
  } catch (e) {
    // 该用户还没有私人集合时后端返回 404，按「暂无语料」处理
    if (e?.status === 404) {
      items.value = []
      total.value = 0
      totalChars.value = 0
    } else {
      ElMessage.error(e.message || '语料加载失败')
    }
  } finally {
    loading.value = false
  }
}

/**
 * 统一的加载调度：所有查询条件变化都走这里，并做 150ms 防抖。
 * 这样「切筛选条件」和「页码归 1」在同一次交互里只会发出一请求，不会重复拉取。
 */
let timer = null
function scheduleLoad(resetPage = false) {
  if (resetPage) page.value = 1
  clearTimeout(timer)
  timer = setTimeout(load, 150)
}

// 查询条件变化（含每页条数）都要回到第 1 页：换页大小后原页码可能已越界。
// 只需监听 page 本身的变化，避免「归 1」又触发一次多余的请求。
watch([keyword, kbFilter, docFilter, pageSize], () => scheduleLoad(true))
watch(page, () => scheduleLoad(false))

onMounted(() => {
  window.addEventListener('resize', onResize)
  load()
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  clearTimeout(timer)
})

// 公共库语料仅管理员可浏览（与「导入审核」同口径），普通用户只提供私人库一项
const kbOptions = computed(() =>
  isAdmin.value
    ? [
        { label: '全部知识库', value: 'all' },
        { label: '公共知识库（公共）', value: 'public' },
        { label: '我的知识库（私人）', value: 'private' }
      ]
    : [{ label: '我的知识库（私人）', value: 'private' }]
)

const docOptions = computed(() => [
  { label: '全部文档', value: 'all' },
  ...documents.value.map((name) => ({ label: name, value: name }))
])

/** 切知识库范围时清掉文档筛选：可选项随范围变化，留着会命中不到任何数据 */
function onKbChange() {
  docFilter.value = 'all'
}

/** 汇总卡片取后端给的整段合计，口径与 total 一致（不是只统计当前页） */
const summary = computed(() => ({
  count: total.value,
  chars: totalChars.value,
  scopeText: isAdmin.value ? '公共 + 我的私人知识库' : '我的私人知识库'
}))

function resetFilters() {
  keyword.value = ''
  kbFilter.value = isAdmin.value ? 'all' : 'private'
  docFilter.value = 'all'
  ElMessage.success('筛选条件已重置')
}

function openDetail(row) {
  detail.value = row
  drawerOpen.value = true
}

function goMine() {
  router.push('/knowledge/mine')
}

const typeMeta = TYPE_META
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">知识库语料</h1>
        <!-- 只留普通用户需要知道的那句：公共库不在这里展示原文。
             管理员口径顶部的视角标签已经说明了，不用再重复一遍。 -->
        <p v-if="!isAdmin" class="page-sub">这里只展示你私人知识库的语料；公共知识库的语料会在提问时参与检索。</p>
      </div>
      <div class="head-actions">
        <el-tag :type="isAdmin ? 'danger' : 'primary'" effect="light" round>
          {{ isAdmin ? '管理员视角 · 公共 + 我的私人' : '普通用户 · 我的私人知识库' }}
        </el-tag>
        <el-button type="primary" plain @click="goMine">前往我的知识库</el-button>
      </div>
    </div>

    <div class="summary">
      <div class="summary__item">
        <span class="summary__label">命中分段</span>
        <span class="summary__value">{{ summary.count }} <em>段</em></span>
      </div>
      <div class="summary__item">
        <span class="summary__label">累计字符</span>
        <span class="summary__value">{{ summary.chars.toLocaleString() }} <em>字</em></span>
      </div>
      <div class="summary__item">
        <span class="summary__label">检索可见范围</span>
        <span class="summary__value summary__value--sm">{{ summary.scopeText }}</span>
      </div>
    </div>

    <section class="panel filter-panel">
      <el-input
        v-model="keyword"
        class="filter-item filter-item--grow"
        placeholder="搜索分段内容、文档名或分段 ID（回车或停止输入后查询）"
        clearable
      >
        <template #prefix>
          <svg viewBox="0 0 24 24" width="16" height="16"><circle cx="11" cy="11" r="6.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M16 16l4.5 4.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
        </template>
      </el-input>
      <el-select v-model="kbFilter" class="filter-item" :disabled="!isAdmin" @change="onKbChange">
        <el-option v-for="o in kbOptions" :key="o.value" :label="o.label" :value="o.value" />
      </el-select>
      <el-select v-model="docFilter" class="filter-item">
        <el-option v-for="o in docOptions" :key="o.value" :label="o.label" :value="o.value" />
      </el-select>
      <el-button @click="resetFilters">重置</el-button>
    </section>

    <section class="panel table-panel">
      <el-table v-loading="loading" :data="items" style="width: 100%" stripe @row-click="openDetail">
        <el-table-column prop="id" label="分段 ID" width="290" show-overflow-tooltip />
        <el-table-column label="所属知识库" min-width="170">
          <template #default="{ row }">
            <el-tag size="small" :type="row.scope === 'public' ? 'warning' : 'primary'" effect="light">
              {{ row.scope === 'public' ? '公共' : '私人' }}
            </el-tag>
            <span class="kb-name">{{ row.kbName }}</span>
          </template>
        </el-table-column>
        <el-table-column label="来源文档" min-width="200">
          <template #default="{ row }">
            <div class="doc-cell">
              <span class="file-badge" :style="{ background: typeMeta[row.docType]?.color }">
                {{ typeMeta[row.docType]?.label || row.docType }}
              </span>
              <span class="doc-cell__name">{{ row.docName }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="seq" label="分段序号" width="100" align="center">
          <template #default="{ row }">#{{ row.seq + 1 }}</template>
        </el-table-column>
        <el-table-column prop="chars" label="字符数" width="90" align="center" />
        <el-table-column prop="createdAt" label="入库时间" width="150" />
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click.stop="openDetail(row)">查看</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <div class="empty">
            <p>{{ isAdmin ? '没有符合条件的语料分段' : '你的私人知识库还没有语料分段，可先上传文档' }}</p>
            <el-button link type="primary" @click="resetFilters">清除筛选条件</el-button>
            <el-button link type="primary" @click="goMine">去上传文档</el-button>
          </div>
        </template>
      </el-table>

      <div class="pager">
        <!-- total 来自后端同一筛选条件下的总行数，换页/换每页条数都会重新查库 -->
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :page-sizes="[8, 16, 32]"
          :total="total"
          layout="total, sizes, prev, pager, next"
          background
        />
      </div>
    </section>

    <el-drawer v-model="drawerOpen" :size="drawerSize" :title="detail?.id" class="detail-drawer">
      <template v-if="detail">
        <div class="detail-head">
          <h3>{{ detail.docName }} · 第 {{ detail.seq + 1 }} 段</h3>
          <el-tag size="small" :type="detail.scope === 'public' ? 'warning' : 'primary'" effect="light">
            {{ detail.scope === 'public' ? '公共知识库' : '私人知识库' }}
          </el-tag>
        </div>

        <div class="meta-grid">
          <div class="meta-item"><span>所属知识库</span><strong>{{ detail.kbName }}</strong></div>
          <div class="meta-item"><span>可见范围</span><strong>{{ detail.scope === 'public' ? '全员可检索' : '仅知识库创建者' }}</strong></div>
          <div class="meta-item"><span>来源文档</span><strong>{{ detail.docName }}</strong></div>
          <div class="meta-item"><span>分段序号</span><strong>第 {{ detail.seq + 1 }} 段</strong></div>
          <div class="meta-item"><span>字符数</span><strong>{{ detail.chars }}</strong></div>
          <div class="meta-item"><span>向量模型</span><strong>{{ detail.model }}</strong></div>
          <div class="meta-item"><span>向量维度</span><strong>{{ detail.dim }}</strong></div>
          <div class="meta-item"><span>向量标识</span><strong>{{ detail.vectorId }}</strong></div>
          <div class="meta-item"><span>入库时间</span><strong>{{ detail.createdAt }}</strong></div>
        </div>

        <h4 class="detail-label">分段正文</h4>
        <div class="detail-content">{{ detail.content }}</div>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.head-actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.summary {
  display: grid;
  /* auto-fit：三项指标也能铺满，不留空格 */
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--space-4);
  margin-bottom: var(--space-5);
}

.summary__item {
  background: linear-gradient(140deg, #ffffff, #f7f9ff);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.summary__label {
  font-size: 12px;
  color: var(--color-text-muted);
}

.summary__value {
  font-size: 22px;
  font-weight: 700;
  color: var(--color-text);
}

.summary__value em {
  font-size: 12px;
  font-style: normal;
  font-weight: 500;
  color: var(--color-text-muted);
  margin-left: 2px;
}

.summary__value--sm {
  font-size: 15px;
  color: var(--color-primary);
  word-break: break-all;
}

.filter-panel {
  display: flex;
  gap: var(--space-3);
  padding: var(--space-4);
  margin-bottom: var(--space-4);
  flex-wrap: wrap;
}

.filter-item {
  width: 240px;
}

.filter-item--grow {
  flex: 1;
  min-width: 200px;
}

.table-panel {
  padding: var(--space-4) var(--space-4) var(--space-3);
  overflow: hidden;
}

.table-panel :deep(.el-table__row) {
  cursor: pointer;
}

.kb-name {
  margin-left: 6px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.doc-cell {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.doc-cell__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}

.file-badge {
  font-size: 11px;
  color: #fff;
  border-radius: var(--radius-sm);
  padding: 2px 7px;
  flex-shrink: 0;
}

.pager {
  display: flex;
  justify-content: flex-end;
  padding-top: var(--space-4);
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
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}

.detail-head h3 {
  font-size: 16px;
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

.meta-item span {
  font-size: 11px;
  color: var(--color-text-muted);
}

.meta-item strong {
  font-size: 13px;
  font-weight: 600;
  word-break: break-all;
}

.detail-label {
  margin: var(--space-5) 0 var(--space-2);
  font-size: 14px;
}

.detail-content {
  background: var(--color-surface-alt);
  border: 1px solid var(--color-border);
  border-left: 3px solid var(--color-primary);
  border-radius: var(--radius-sm);
  padding: var(--space-4);
  font-size: 13px;
  line-height: 1.9;
  color: var(--color-text-secondary);
}

@media (max-width: 1023px) {
  .summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .filter-item,
  .filter-item--grow {
    width: 100%;
    flex: 1 1 100%;
  }
}

@media (max-width: 767px) {
  .summary {
    grid-template-columns: 1fr;
  }

  .meta-grid {
    grid-template-columns: 1fr;
  }

  .table-panel {
    padding: var(--space-3);
  }

  .pager {
    justify-content: center;
  }
}
</style>
