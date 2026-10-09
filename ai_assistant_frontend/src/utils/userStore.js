import { reactive, computed } from 'vue'
import { clearToken, getToken } from '@/api/request'

const STORAGE_KEY = 'zhiyu_user'

function readUser() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null')
  } catch (e) {
    return null
  }
}

// 本地有用户信息但没有 token，说明凭证已失效，按未登录处理
const cachedUser = readUser()
const state = reactive({
  user: cachedUser && getToken() ? cachedUser : null
})

if (!state.user) {
  localStorage.removeItem(STORAGE_KEY)
  clearToken()
}

export function useUserStore() {
  const isLoggedIn = computed(() => !!state.user)
  // 后端 UserVO 暂未返回 role，管理员身份无法通过登录接口获得
  const isAdmin = computed(() => state.user?.role === 'admin')
  const displayName = computed(() => state.user?.name || '访客')

  function setUser(user) {
    state.user = user
    if (user) localStorage.setItem(STORAGE_KEY, JSON.stringify(user))
    else localStorage.removeItem(STORAGE_KEY)
  }

  function logout() {
    state.user = null
    localStorage.removeItem(STORAGE_KEY)
    clearToken()
  }

  return { state, isLoggedIn, isAdmin, displayName, setUser, logout }
}
