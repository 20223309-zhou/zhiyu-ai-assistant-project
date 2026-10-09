<script setup>
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppNavbar from '@/components/AppNavbar.vue'
import { fetchMe } from '@/api'
import { useUserStore } from '@/utils/userStore'

const route = useRoute()
const router = useRouter()
const { isLoggedIn, setUser, logout } = useUserStore()

const isAuthPage = computed(() => route.name === 'login')

// 版权年份取运行时年份，不写死
const year = new Date().getFullYear()

// 启动时用本地 token 校验一次登录态：token 过期或已登出（token_version 变化）时
// 后端返回 401，这里清掉本地凭证并回到登录页。
onMounted(async () => {
  if (!isLoggedIn.value) return
  try {
    setUser({ ...(await fetchMe()) })
  } catch (e) {
    if (e?.status === 401) {
      logout()
      router.replace({ name: 'login' })
    }
  }
})
</script>

<template>
  <div class="app-shell">
    <AppNavbar v-if="!isAuthPage" />

    <main class="app-main">
      <router-view v-slot="{ Component }">
        <transition name="fade-slide" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </main>

    <footer v-if="!isAuthPage" class="app-footer">
      <div class="app-footer__inner">
        <span>© {{ year }} 智语 AI · 企业知识库对话助手</span>
      </div>
    </footer>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100%;
  display: flex;
  flex-direction: column;
  background:
    radial-gradient(900px 400px at 12% -8%, rgba(79, 124, 255, 0.1), transparent 70%),
    var(--color-bg);
}

.app-main {
  flex: 1;
  min-height: 0;
}

.app-footer {
  border-top: 1px solid var(--color-border);
  background: rgba(255, 255, 255, 0.7);
}

.app-footer__inner {
  /* 与顶栏同宽：顶栏通栏、页脚也通栏，上下才呼应（中间正文仍 1280 居中） */
  padding: var(--space-4) var(--space-6);
  font-size: 12px;
  color: var(--color-text-muted);
}

.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}

.fade-slide-enter-from {
  opacity: 0;
  transform: translateY(8px);
}

.fade-slide-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

/* 与顶栏的断点保持一致：窄屏左右留白收窄，避免通栏后显得贴边 */
@media (max-width: 900px) {
  .app-footer__inner {
    padding: var(--space-4) var(--space-4);
  }
}
</style>
