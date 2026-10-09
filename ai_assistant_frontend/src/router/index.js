import { createRouter, createWebHashHistory } from 'vue-router'
import { useUserStore } from '@/utils/userStore'

const routes = [
  { path: '/', redirect: '/chat' },
  {
    path: '/login',
    name: 'login',
    component: () => import('@/pages/LoginPage.vue'),
    meta: { guestOnly: true, title: '登录 / 注册' }
  },
  {
    path: '/chat',
    name: 'chat',
    component: () => import('@/pages/ChatPage.vue'),
    meta: { requiresAuth: true, title: 'AI 对话' }
  },
  {
    path: '/knowledge/mine',
    name: 'knowledge-mine',
    component: () => import('@/pages/MyKnowledgePage.vue'),
    meta: { requiresAuth: true, title: '我的知识库' }
  },
  {
    path: '/knowledge/import',
    redirect: '/knowledge/mine'
  },
  {
    path: '/knowledge/review',
    name: 'knowledge-review',
    component: () => import('@/pages/ReviewPage.vue'),
    meta: { requiresAuth: true, requiresAdmin: true, title: '导入审核' }
  },
  {
    path: '/knowledge/corpus',
    name: 'knowledge-corpus',
    component: () => import('@/pages/CorpusPage.vue'),
    meta: { requiresAuth: true, title: '知识库语料' }
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/pages/NotFoundPage.vue'),
    meta: { title: '页面不存在' }
  }
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 })
})

router.beforeEach((to) => {
  const { isLoggedIn, isAdmin } = useUserStore()

  if (to.meta.requiresAuth && !isLoggedIn.value) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.meta.requiresAdmin && !isAdmin.value) {
    return { name: 'chat', query: { denied: '1' } }
  }
  if (to.meta.guestOnly && isLoggedIn.value) {
    return { name: 'chat' }
  }
  return true
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · 智语 AI` : '智语 AI · 企业知识库对话助手'
})

export default router
