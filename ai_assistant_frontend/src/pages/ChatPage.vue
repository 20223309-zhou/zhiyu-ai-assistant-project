<script setup>
import { ref, reactive, computed, nextTick, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createConversation,
  deleteConversation,
  getModels,
  listConversations,
  listMessages,
  streamChat,
  SUGGESTIONS
} from '@/api'
import { useUserStore } from '@/utils/userStore'
import MarkdownBlock from '@/components/MarkdownBlock.vue'
// 自己画的示意图，替换原来爬来的带水印外链图
import welcomeImg from '@/assets/welcome-ai.svg'
// 对话中代表 AI 的图标（设计稿 avatar.png），用在每条 AI 回复左侧
import aiAvatar from '@/assets/ai-avatar.png'

const route = useRoute()
const { displayName, isAdmin } = useUserStore()

const roleLabel = computed(() => (isAdmin.value ? '管理员' : '普通用户'))

/**
 * 侧栏底部的身份行。
 *
 * 用户名有可能本身就等于角色词（种子里 admin 的 username 就叫「管理员」），
 * 直接拼「name · role」会渲染成「管理员 · 管理员」。相同就只显示一个。
 */
const sideIdentity = computed(() => {
  const name = displayName.value || ''
  return !name || name === roleLabel.value ? roleLabel.value : `${name} · ${roleLabel.value}`
})

/** 是否启用知识库检索。偏好存在本地，刷新后保持；默认开启（本产品定位就是知识库问答） */
const RAG_PREF_KEY = 'zhiyu_use_rag'
const useRag = ref(localStorage.getItem(RAG_PREF_KEY) !== '0')
watch(useRag, (val) => localStorage.setItem(RAG_PREF_KEY, val ? '1' : '0'))

/**
 * 流式渲染的合并间隔（毫秒）。
 * 每个 token 都触发一次 MarkdownBlock 的全量 marked.parse + DOMPurify.sanitize，
 * 不节流的话长回复会明显掉帧，所以按固定间隔把累积文本刷进界面。
 */
const RENDER_INTERVAL = 80

/** 模型选择：清单来自后端 /model/get，选择结果存本地 */
const MODEL_PREF_KEY = 'zhiyu_model'
const modelOptions = ref([])
const selectedModel = ref(localStorage.getItem(MODEL_PREF_KEY) || '')
watch(selectedModel, (val) => {
  if (val) localStorage.setItem(MODEL_PREF_KEY, val)
})

/** 侧边栏会话列表，来自 GET /conversation/list（后端按创建时间倒序） */
const conversations = ref([])
/** 当前会话的消息，来自 GET /conversation/messages/list */
const messages = ref([])
const input = ref('')
const sending = ref(false)
const loadingMessages = ref(false)
const scrollRef = ref(null)
const sidebarOpen = ref(false)
const activeTitle = ref('新的对话')
/** 当前会话 id（后端 conversation.id，UUID 字符串）；空串表示「尚未落库的新对话」 */
const currentId = ref('')
/** 流式请求的中止句柄，供「停止生成」用 */
const streamAbort = ref(null)

onMounted(async () => {
  if (route.query.denied === '1') {
    ElMessage.warning('该功能仅对管理员开放，请联系知识运营部开通权限')
  }
  await Promise.all([loadConversations(), loadModels()])
})

onBeforeUnmount(() => {
  // 离开页面时掐断还在跑的流，别让连接悬着
  streamAbort.value?.abort()
})

async function loadModels() {
  try {
    const list = await getModels()
    modelOptions.value = list
    const names = list.map((m) => m.name)
    // 本地存过的模型可能已下线，回落到后端标记的默认项
    if (!selectedModel.value || !names.includes(selectedModel.value)) {
      selectedModel.value = (list.find((m) => m.isDefault) || list[0])?.name || ''
    }
  } catch (e) {
    ElMessage.error(e.message || '模型列表加载失败')
  }
}

function scrollToBottom() {
  nextTick(() => {
    const el = scrollRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function markActive(id) {
  conversations.value.forEach((c) => (c.active = c.id === id))
}

async function loadConversations() {
  try {
    const list = await listConversations()
    conversations.value = list.map((c) => ({ ...c, active: c.id === currentId.value }))
    const current = list.find((c) => c.id === currentId.value)
    if (current) activeTitle.value = current.title
  } catch (e) {
    ElMessage.error(e.message || '会话列表加载失败')
  }
}

async function loadMessages(id) {
  loadingMessages.value = true
  try {
    messages.value = await listMessages(id)
  } catch (e) {
    ElMessage.error(e.message || '历史消息加载失败')
    messages.value = []
  } finally {
    loadingMessages.value = false
  }
}

async function send(text) {
  const question = (text ?? input.value).trim()
  if (!question || sending.value) return

  messages.value.push({ id: `local-u-${Date.now()}`, role: 'user', content: question })
  input.value = ''
  sending.value = true

  // 助手气泡先占位，逐 token 往里追加。phase 用来决定气泡里先显示什么：
  // 开了知识库检索时首 token 要等两路向量检索，界面上得让用户知道在干什么。
  const assistant = reactive({
    id: `local-a-${Date.now()}`,
    role: 'assistant',
    content: '',
    model: selectedModel.value,
    streaming: true,
    phase: useRag.value ? 'retrieving' : 'thinking'
  })
  messages.value.push(assistant)

  let raw = ''
  let flushTimer = null
  const flush = () => {
    assistant.content = raw
    scrollToBottom()
  }

  streamAbort.value = new AbortController()

  try {
    // 首次提问时才创建会话：后端要求 session_id 必须已存在
    if (!currentId.value) {
      const conv = await createConversation(question.slice(0, 20))
      currentId.value = conv.id
      activeTitle.value = conv.title || question.slice(0, 18)
      await loadConversations()
      markActive(conv.id)
    }

    // 按固定间隔把累积文本刷进界面：每个 token 都重渲染 Markdown 会掉帧
    flushTimer = setInterval(flush, RENDER_INTERVAL)

    // 知识库开关和模型都按「发送那一刻」的状态传，中途改动不影响已发出的问题
    await streamChat(
      {
        sessionId: currentId.value,
        query: question,
        ragFlag: useRag.value,
        modelName: selectedModel.value,
        signal: streamAbort.value.signal
      },
      {
        onToken: (chunk) => {
          raw += chunk
          if (assistant.phase !== 'generating') assistant.phase = 'generating'
        },
        onDone: (payload) => {
          // 后端落库失败时 status=error，界面上的内容仍在，但要提醒用户没存住
          if (payload?.status === 'error') ElMessage.warning('回答已生成，但服务端未能完整保存')
        },
        onError: (err) => {
          ElMessage.error(err.message || '生成失败，请稍后重试')
        }
      }
    )
  } catch (e) {
    if (e?.name === 'AbortError') {
      // 用户主动停止：已生成的部分保留在界面上，服务端也会存下（状态标为 error）
      ElMessage.info('已停止生成')
    } else {
      ElMessage.error(e.message || '对话服务暂时不可用，请稍后重试')
    }
  } finally {
    clearInterval(flushTimer)
    assistant.streaming = false
    // 收尾时补齐最后一段，别把节流窗口内的内容丢掉
    assistant.content = raw
    sending.value = false
    streamAbort.value = null
    scrollToBottom()
  }
}

/** 停止生成：掐断流，已收到的内容留在界面上 */
function stopGenerating() {
  streamAbort.value?.abort()
}

function newChat() {
  currentId.value = ''
  messages.value = []
  activeTitle.value = '新的对话'
  markActive('')
  sidebarOpen.value = false
}

async function pickConversation(item) {
  sidebarOpen.value = false
  if (item.id === currentId.value) return
  currentId.value = item.id
  activeTitle.value = item.title
  markActive(item.id)
  await loadMessages(item.id)
  scrollToBottom()
}

/** 删除会话（连同消息），二次确认后调用 DELETE /conversation/{id} */
async function removeConversation(item) {
  if (!item?.id) return
  try {
    await ElMessageBox.confirm(
      `「${item.title}」的对话记录将被永久删除，无法恢复。`,
      '删除会话',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
  } catch (e) {
    return // 用户取消
  }

  try {
    const res = await deleteConversation(item.id)
    ElMessage.success(res.deletedMessages ? `已删除会话及其 ${res.deletedMessages} 条消息` : '已删除会话')
    if (!res.checkpointCleared) {
      // 会话本身已删除，只是后端清 langgraph checkpoint 失败，如实告知而不是假装成功
      ElMessage.warning('会话已删除，但服务端上下文缓存未完全清理')
    }
  } catch (e) {
    ElMessage.error(e.message || '删除失败，请稍后重试')
    return
  }

  // 删掉的正好是当前会话 → 回到「新对话」空白态
  if (item.id === currentId.value) {
    currentId.value = ''
    messages.value = []
    activeTitle.value = '新的对话'
  }
  // 重新拉列表：loadConversations 会按 currentId 重新计算高亮
  await loadConversations()
}

/** 顶部「删除对话」按钮：删当前会话 */
function removeCurrentConversation() {
  const item = conversations.value.find((c) => c.id === currentId.value)
  if (item) removeConversation(item)
}

/** 标题右侧「更多」菜单的命令分发 */
function onHeadCommand(command) {
  if (command === 'delete') removeCurrentConversation()
}
</script>

<template>
  <div class="chat">
    <aside class="chat__side" :class="{ 'is-open': sidebarOpen }">
      <div class="side-inner">
        <el-button type="primary" class="new-btn" @click="newChat">
          <svg viewBox="0 0 24 24" width="16" height="16" style="margin-right: 6px">
            <path d="M12 5v14M5 12h14" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" />
          </svg>
          发起新对话
        </el-button>

        <p class="side-label">最近对话</p>
        <div class="conv-list">
          <p v-if="!conversations.length" class="conv-empty">暂无会话记录</p>
          <!-- 用 div 而不是 button：里面还要放删除按钮，button 不能嵌套 button -->
          <div
            v-for="item in conversations"
            :key="item.id"
            class="conv-item"
            :class="{ 'is-active': item.active }"
            role="button"
            tabindex="0"
            @click="pickConversation(item)"
            @keydown.enter.prevent="pickConversation(item)"
          >
            <span class="conv-title">{{ item.title }}</span>
            <span class="conv-time">{{ item.createdAt }}</span>
            <button
              type="button"
              class="conv-del"
              title="删除该会话"
              aria-label="删除该会话"
              @click.stop="removeConversation(item)"
            >
              <svg viewBox="0 0 24 24" width="14" height="14">
                <path d="M4 7h16M9 7V5h6v2M6 7l1 12h10l1-12" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </button>
          </div>
        </div>

        <div class="side-tip">
          <span>{{ sideIdentity }}</span>
        </div>
      </div>
    </aside>

    <section class="chat__main">
      <header class="chat__head">
        <button class="icon-btn only-mobile" aria-label="会话列表" @click="sidebarOpen = !sidebarOpen">
          <svg viewBox="0 0 24 24" width="20" height="20">
            <path d="M4 6h16M4 12h16M4 18h16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
          </svg>
        </button>
        <div class="chat__head-text">
          <h2>{{ activeTitle }}</h2>
          <!-- 这里的检索状态必须跟下面的开关联动，否则会出现「顶部说已开启、开关是关闭」的矛盾 -->
          <span class="chat__head-sub" :class="{ 'is-off': !useRag }">
            <template v-if="useRag">
              <i class="dot"></i> 知识检索已开启 · 命中范围：公共知识库{{ isAdmin ? ' + 全部私人知识库（管理员视角）' : ' + 我的私人知识库' }}
            </template>
            <template v-else>
              <i class="dot"></i> 知识检索已关闭 · 回答由模型直接生成
            </template>
          </span>
        </div>
        <!-- 删除是破坏性操作，不再跟标题挤在一排（那样太容易误点）。
             收进「更多」菜单，离标题远一点，也留出以后加其它动作的位置。 -->
        <el-dropdown trigger="click" :disabled="!currentId" @command="onHeadCommand">
          <button class="icon-btn" type="button" aria-label="更多操作" :disabled="!currentId">
            <svg viewBox="0 0 24 24" width="18" height="18">
              <circle cx="12" cy="5" r="1.7" fill="currentColor" />
              <circle cx="12" cy="12" r="1.7" fill="currentColor" />
              <circle cx="12" cy="19" r="1.7" fill="currentColor" />
            </svg>
          </button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="delete">
                <span class="menu-danger">删除该对话</span>
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </header>

      <div ref="scrollRef" v-loading="loadingMessages" class="chat__stream">
        <div v-if="!messages.length" class="welcome">
          <img class="welcome__img" :src="welcomeImg" alt="" aria-hidden="true" />
          <h3>你好，{{ displayName }}，我是智语 AI</h3>
          <p>基于企业知识库为你答疑，试试这些问题：</p>
          <!-- 一排 chip 而不是 2×2 卡片：卡片占的纵向空间太多，
               首屏视线会被插画和建议区拉走，看不到输入框 -->
          <div class="suggest-row">
            <button v-for="s in SUGGESTIONS" :key="s" class="suggest-chip" @click="send(s)">
              {{ s }}
            </button>
          </div>
        </div>

        <div v-for="msg in messages" :key="msg.id" class="msg" :class="`msg--${msg.role}`">
          <img
            v-if="msg.role === 'assistant'"
            class="msg__avatar msg__avatar--ai"
            :src="aiAvatar"
            alt=""
            aria-hidden="true"
          />
          <div class="msg__body">
            <div
              class="msg__bubble"
              :class="{ 'msg__bubble--empty': !msg.content, 'msg__bubble--md': msg.content && msg.role === 'assistant' }"
            >
              <!-- 流式过程中先给个状态，避免刚发出时空气泡闪一下 -->
              <div v-if="msg.streaming && !msg.content" class="msg__phase">
                <span class="typing"><i></i><i></i><i></i></span>
                <em>{{ msg.phase === 'retrieving' ? '正在检索知识库…' : '正在思考…' }}</em>
              </div>
              <!-- 历史数据里存在 content 为空的消息（模型当次未返回内容），给个占位避免渲染出空气泡 -->
              <template v-else-if="!msg.content">（该条消息内容为空，可重新提问）</template>
              <!-- 模型回复按 Markdown 渲染；用户提问保持纯文本，避免把自己的输入当格式解析 -->
              <MarkdownBlock v-else-if="msg.role === 'assistant'" :content="msg.content" />
              <template v-else>{{ msg.content }}</template>
            </div>
            <!-- 用的是哪个模型，切换模型后能看出来 -->
            <div v-if="msg.role === 'assistant' && msg.model" class="msg__meta">
              {{ msg.model }}
              <template v-if="msg.streaming"> · 生成中</template>
            </div>
            <div v-if="msg.citations?.length" class="citations">
              <span class="citations__label">引用来源</span>
              <span v-for="(c, i) in msg.citations" :key="i" class="citation">
                {{ c.docName }} · 第 {{ c.seq }} 段
              </span>
            </div>
          </div>
          <span v-if="msg.role === 'user'" class="msg__avatar msg__avatar--user">{{ displayName.slice(0, 1) }}</span>
        </div>
      </div>

      <!-- 开关、模型、输入框、发送全在一个容器里。
           原来开关+模型单独占一行、输入框在下一行，视觉上是两个割裂的块。 -->
      <footer class="composer">
        <div class="composer__box">
          <el-input
            v-model="input"
            type="textarea"
            :autosize="{ minRows: 1, maxRows: 5 }"
            resize="none"
            placeholder="输入你的问题，Enter 发送，Shift + Enter 换行"
            @keydown.enter.exact.prevent="send()"
          />
          <div class="composer__foot">
            <div class="composer__toolbar">
              <el-switch v-model="useRag" size="small" />
              <span class="composer__switch-label">知识库检索</span>
              <el-tooltip
                placement="top"
                :content="useRag
                  ? '先检索知识库，只依据检索到的内容作答；未命中时回复「根据已知信息无法回答该问题」'
                  : '不检索知识库，直接由模型回答'"
              >
                <span class="composer__switch-state" :class="{ 'is-on': useRag }">
                  {{ useRag ? '已开启' : '已关闭' }}
                </span>
              </el-tooltip>

              <span class="composer__divider"></span>

              <!-- 模型清单来自 /model/get，不在前端硬编码 -->
              <el-select v-model="selectedModel" size="small" class="composer__model" placeholder="选择模型">
                <el-option v-for="m in modelOptions" :key="m.name" :label="m.label" :value="m.name" />
              </el-select>
            </div>
            <!-- 生成中变成「停止」：流式可以中途掐断，已生成的内容会保留 -->
            <el-button v-if="sending" class="send-btn" @click="stopGenerating">停止</el-button>
            <el-button v-else type="primary" class="send-btn" @click="send()">发送</el-button>
          </div>
        </div>
      </footer>
    </section>

    <div v-if="sidebarOpen" class="side-mask" @click="sidebarOpen = false"></div>
  </div>
</template>

<style scoped>
/* 对话页比其它页面宽：它内部还要放 272px 侧栏，1280 的容器在 1920 屏上两侧留白过大。
   分两档，避免小屏被撑开。 */
.chat {
  max-width: 1440px;
  margin: 0 auto;
  padding: var(--space-5);
  display: grid;
  grid-template-columns: 272px 1fr;
  gap: var(--space-5);
  height: calc(100vh - var(--nav-height) - 50px);
  min-height: 560px;
}

@media (min-width: 1600px) {
  .chat {
    max-width: 1680px;
  }
}

.chat__side {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}

.side-inner {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: var(--space-4);
  gap: var(--space-4);
}

.new-btn {
  width: 100%;
}

.side-label {
  font-size: 12px;
  color: var(--color-text-muted);
  letter-spacing: 0.5px;
}

.conv-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin: calc(var(--space-2) * -1) 0 0;
}

.conv-empty {
  padding: var(--space-3) var(--space-2);
  font-size: 12px;
  line-height: 1.7;
  color: var(--color-text-muted);
}

.conv-item {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 2px;
  text-align: left;
  border: none;
  background: transparent;
  border-radius: var(--radius-sm);
  /* 右侧常留删除按钮的位置：只在 hover 时挤出来会导致文字位置跳动 */
  padding: 10px 34px 10px 12px;
  font-family: inherit;
  cursor: pointer;
  transition: all var(--transition);
}

/* 删除按钮：平时隐藏并禁用点击（否则是个看不见但能点的陷阱按钮） */
.conv-del {
  position: absolute;
  top: 50%;
  right: 8px;
  transform: translateY(-50%);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  padding: 0;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-muted);
  cursor: pointer;
  opacity: 0;
  pointer-events: none;
  transition: all var(--transition);
}

.conv-item:hover .conv-del,
.conv-item:focus-within .conv-del,
.conv-item.is-active .conv-del {
  opacity: 1;
  pointer-events: auto;
}

.conv-del:hover {
  background: var(--color-danger);
  color: #fff;
}

.conv-item:hover {
  background: var(--color-surface-alt);
}

.conv-item.is-active {
  background: var(--color-primary-soft);
}

.conv-title {
  font-size: 13px;
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.conv-item.is-active .conv-title {
  color: var(--color-primary);
  font-weight: 600;
}

.conv-time {
  font-size: 11px;
  color: var(--color-text-muted);
}

.side-tip {
  border-top: 1px solid var(--color-border);
  padding-top: var(--space-3);
  font-size: 12px;
  color: var(--color-text-secondary);
}

.chat__main {
  display: flex;
  flex-direction: column;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
  min-width: 0;
}

.chat__head {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface-alt);
}

.chat__head-text {
  flex: 1;
  min-width: 0;
}

.chat__head h2 {
  font-size: 16px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chat__head-sub {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--color-text-muted);
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  /* 强调色。这里表达的是「检索通道已开启」，与下方开关的「已开启」同色 ——
     两处说的是同一件事，颜色必须一致；把 --color-success 留给真正的成功结果 */
  background: var(--color-accent);
}

/* 检索关闭时状态点变灰，避免绿色暗示「正在检索」 */
.chat__head-sub.is-off .dot {
  background: var(--color-text-muted);
}

.icon-btn {
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  border-radius: var(--radius-sm);
  width: 36px;
  height: 36px;
  /* 必须显式 inline-flex：下面还有 .only-mobile { display: none } 会在桌面端盖掉它，
     两边顺序已经对了，但这里不能漏，否则图标不会居中 */
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all var(--transition);
}

.icon-btn:hover:not(:disabled) {
  border-color: var(--color-primary-light);
  color: var(--color-primary);
}

.icon-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.chat__stream {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-5);
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  background:
    radial-gradient(600px 300px at 80% 0%, rgba(79, 124, 255, 0.06), transparent 70%),
    var(--color-surface);
}

.welcome {
  margin: auto;
  max-width: 720px;
  text-align: center;
}

/* 插画是透明底，用 contain 保证图案不被裁；不再套圆角和投影，
   否则又变成"贴了一张图"的观感。
   高度压到 120 —— 首屏纵向空间要留给输入框，不是留给插画。 */
.welcome__img {
  width: 100%;
  max-width: 320px;
  height: 120px;
  object-fit: contain;
}

.welcome h3 {
  font-size: 20px;
  margin: var(--space-4) 0 var(--space-1);
}

.welcome p {
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.8;
}

/* 建议问题：一行 chip，可换行。比 2×2 卡片省一大截纵向空间 */
.suggest-row {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: var(--space-2);
  margin-top: var(--space-4);
}

.suggest-chip {
  font-family: inherit;
  font-size: 13px;
  line-height: 1.4;
  padding: 7px 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  background: var(--color-surface);
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all var(--transition);
}

.suggest-chip:hover {
  border-color: var(--color-primary-light);
  background: var(--color-primary-soft);
  color: var(--color-primary);
}

.msg {
  display: flex;
  gap: var(--space-3);
  align-items: flex-start;
  max-width: 100%;
}

.msg--user {
  justify-content: flex-end;
}

.msg__avatar {
  flex-shrink: 0;
  width: 34px;
  height: 34px;
  border-radius: 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  color: #fff;
  /* 原来两个头像各写了两组硬编码渐变（蓝 + 另一种绿），
     现在统一走 token：AI = 主题色，用户 = 强调色。少一种几乎撞色的绿。 */
  background: var(--color-primary);
}

/* AI 侧换成品牌图形：图形自带颜色与轮廓，不要再套底色和圆角
   —— 圆角会把它两侧的耳机和底部的麦克风裁掉 */
.msg__avatar--ai {
  background: none;
  border-radius: 0;
  object-fit: contain;
}

.msg__avatar--user {
  background: var(--color-accent);
}

/* 容器放宽后气泡按百分比会跟着变长，读起来累。
   给一个绝对上限，让多出来的宽度变成留白而不是行长。 */
.msg__body {
  max-width: min(74%, 820px);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.msg--user .msg__body {
  align-items: flex-end;
}

.msg__bubble {
  padding: 12px 16px;
  border-radius: var(--radius-md);
  font-size: 14px;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--color-surface-alt);
  border: 1px solid var(--color-border);
  color: var(--color-text);
}

.msg--user .msg__bubble {
  background: linear-gradient(135deg, #4f7cff, #3a63db);
  border-color: transparent;
  color: #fff;
  border-top-right-radius: 4px;
}

.msg--assistant .msg__bubble {
  border-top-left-radius: 4px;
}

.msg__bubble--empty {
  color: var(--color-text-muted);
  font-style: italic;
}

/* 流式过程中的状态行：转圈点 + 文案（检索阶段会停留得久一点，必须让用户看到在干什么） */
.msg__phase {
  display: flex;
  align-items: center;
  gap: 10px;
}

.msg__phase em {
  font-style: normal;
  font-size: 13px;
  color: var(--color-text-muted);
}

/* 消息下方的小字：这条回答用的是哪个模型 */
.msg__meta {
  margin-top: 5px;
  font-size: 11px;
  color: var(--color-text-muted);
}

/*
 * Markdown 渲染版气泡：markdown 自己会生成 <p>/<li> 等块级元素，
 * 不能让 pre-wrap 再叠一层换行，否则段间距会被撑成双倍。
 */
.msg__bubble--md {
  white-space: normal;
}

/* 助手气泡可以放宽一点，便于展示列表与表格 */
.msg--assistant .msg__body {
  max-width: 76%;
}

.citations {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.citations__label {
  font-size: 12px;
  color: var(--color-text-muted);
}

.citation {
  font-size: 12px;
  color: var(--color-primary);
  background: var(--color-primary-soft);
  border-radius: var(--radius-full);
  padding: 3px 10px;
}

.typing {
  display: flex;
  gap: 5px;
  align-items: center;
}

.typing i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-primary-light);
  animation: blink 1.2s infinite ease-in-out;
}

.typing i:nth-child(2) {
  animation-delay: 0.18s;
}

.typing i:nth-child(3) {
  animation-delay: 0.36s;
}

@keyframes blink {
  0%, 80%, 100% { opacity: 0.25; transform: translateY(0); }
  40% { opacity: 1; transform: translateY(-3px); }
}

.composer {
  border-top: 1px solid var(--color-border);
  padding: var(--space-4) var(--space-5);
  background: var(--color-surface-alt);
}

/* 知识库检索开关：放在输入框上方，状态一眼可见 */
.composer__box {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  background: var(--color-surface);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
  padding: var(--space-2) var(--space-2) var(--space-2) var(--space-4);
  transition: all var(--transition);
}

.composer__box:focus-within {
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px rgba(79, 124, 255, 0.14);
}

.composer__box :deep(.el-textarea__inner) {
  border: none;
  box-shadow: none;
  padding: 8px 0;
  background: transparent;
  font-family: inherit;
  font-size: 14px;
}

/* 容器内的底行：左边是开关与模型，右边是发送 */
.composer__foot {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.composer__toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex: 1;
  min-width: 0;
}

.composer__switch-label {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.composer__switch-state {
  font-size: 12px;
  color: var(--color-text-muted);
  cursor: help;
  border-bottom: 1px dashed var(--color-border-strong);
}

/* 用强调色而不是主题蓝：这是一个「状态」，不该跟链接、按钮抢同一个颜色 */
.composer__switch-state.is-on {
  color: var(--color-accent);
  border-bottom-color: var(--color-accent);
}

.composer__divider {
  width: 1px;
  height: 14px;
  margin: 0 2px;
  background: var(--color-border-strong);
}

.composer__model {
  width: 160px;
}

.send-btn {
  height: 36px;
  padding: 0 22px;
  flex-shrink: 0;
}

/* 更多菜单里的破坏性项：用危险色，但不当成普通按钮的样式 */
.menu-danger {
  color: var(--color-danger);
}

.only-mobile {
  display: none;
}

.side-mask {
  display: none;
}

@media (max-width: 900px) {
  .chat {
    grid-template-columns: 1fr;
    padding: var(--space-4);
    height: auto;
    min-height: calc(100vh - var(--nav-height) - 56px);
    position: relative;
  }

  .chat__side {
    position: fixed;
    top: var(--nav-height);
    bottom: 0;
    left: 0;
    width: 280px;
    z-index: 300;
    border-radius: 0;
    transform: translateX(-100%);
    transition: transform var(--transition);
  }

  .chat__side.is-open {
    transform: translateX(0);
  }

  /* 小屏是抽屉、没有 hover，删除按钮常显，否则点不到 */
  .conv-del {
    opacity: 1;
    pointer-events: auto;
  }

  .side-mask {
    display: block;
    position: fixed;
    inset: 0;
    background: rgba(29, 37, 49, 0.4);
    z-index: 200;
  }

  .only-mobile {
    display: inline-flex;
  }

  .chat__main {
    min-height: calc(100vh - var(--nav-height) - 88px);
  }
}

@media (max-width: 767px) {
  .chat {
    padding: var(--space-3);
  }

  /* 建议问题已经是可换行的 chip，不需要再单独改列数 */
  .msg__body {
    max-width: 88%;
  }

  .chat__stream,
  .chat__head,
  .composer {
    padding-left: var(--space-4);
    padding-right: var(--space-4);
  }

  /* 小屏再矮一点，给输入框留位置 */
  .welcome__img {
    height: 96px;
  }
}
</style>
