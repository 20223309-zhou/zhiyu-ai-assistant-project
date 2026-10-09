<script setup>
import { ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import BrandLogo from './BrandLogo.vue'
import { logout as logoutApi } from '@/api'
import { useUserStore } from '@/utils/userStore'

const route = useRoute()
const router = useRouter()
const { state, isAdmin, displayName, logout } = useUserStore()

const drawerOpen = ref(false)

/**
 * 角色徽标只在「用户名跟角色词不一样」时才显示。
 *
 * 否则会撞成三个「管理员」：徽标一个、头像首字一个、用户名一个 —— 账号 admin 的
 * username 就叫「管理员」，实测就是这样。徽标的作用是补充信息，重复显示反而更乱。
 */
const showRoleTag = computed(() => isAdmin.value && displayName.value !== '管理员')

const navItems = computed(() => {
  const items = [
    { name: 'chat', label: 'AI 对话', to: '/chat' },
    { name: 'knowledge-mine', label: '我的知识库', to: '/knowledge/mine' },
    { name: 'knowledge-corpus', label: '知识库语料', to: '/knowledge/corpus' }
  ]
  if (isAdmin.value) {
    items.splice(2, 0, { name: 'knowledge-review', label: '导入审核', to: '/knowledge/review' })
  }
  return items
})

function go(to) {
  drawerOpen.value = false
  router.push(to)
}

async function handleCommand(command) {
  if (command === 'logout') {
    try {
      await ElMessageBox.confirm('确认退出当前账号吗？', '退出登录', {
        confirmButtonText: '退出',
        cancelButtonText: '取消',
        type: 'warning'
      })
    } catch (e) {
      return // 用户取消
    }
    try {
      // 通知后端使已签发的 token 立即失效
      await logoutApi()
    } catch (e) {
      // 网络异常也不阻塞本地退出
    }
    logout()
    ElMessage.success('已安全退出')
    router.push('/login')
  }
}
</script>

<template>
  <header class="navbar">
    <div class="navbar__inner">
      <BrandLogo :size="36" @click="go('/chat')" class="navbar__brand" />

      <nav class="navbar__nav">
        <button
          v-for="item in navItems"
          :key="item.name"
          class="nav-link"
          :class="{ 'is-active': route.name === item.name }"
          @click="go(item.to)"
        >
          {{ item.label }}
        </button>
      </nav>

      <div class="navbar__right">
        <el-tag v-if="showRoleTag" type="primary" effect="light" round size="small" class="role-tag">
          管理员
        </el-tag>

        <el-dropdown trigger="click" @command="handleCommand">
          <div class="user-chip">
            <span class="avatar">{{ displayName.slice(0, 1) }}</span>
            <span class="user-chip__name">{{ displayName }}</span>
            <svg viewBox="0 0 24 24" width="14" height="14" class="chev">
              <path d="M7 10l5 5 5-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item disabled>{{ state.user?.account }}</el-dropdown-item>
              <el-dropdown-item divider command="logout">退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>

        <button class="hamburger" aria-label="打开菜单" @click="drawerOpen = true">
          <svg viewBox="0 0 24 24" width="22" height="22">
            <path d="M4 7h16M4 12h16M4 17h16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
          </svg>
        </button>
      </div>
    </div>

    <el-drawer v-model="drawerOpen" direction="rtl" size="72%" :with-header="false">
      <div class="drawer-body">
        <BrandLogo :size="40" />
        <nav class="drawer-nav">
          <button
            v-for="item in navItems"
            :key="item.name"
            class="drawer-link"
            :class="{ 'is-active': route.name === item.name }"
            @click="go(item.to)"
          >
            {{ item.label }}
          </button>
        </nav>
        <button class="drawer-logout" @click="handleCommand('logout')">退出登录</button>
      </div>
    </el-drawer>
  </header>
</template>

<style scoped>
.navbar {
  position: sticky;
  top: 0;
  z-index: 100;
  background: rgba(255, 255, 255, 0.88);
  backdrop-filter: saturate(180%) blur(12px);
  border-bottom: 1px solid var(--color-border);
}

.navbar__inner {
  /* 顶栏通栏：不再套 max-width 居中容器，logo 贴视口左、用户框贴视口右。
     页面正文仍走 .page 的 1280 居中，这是常见的「顶栏通栏 + 内容居中」布局。 */
  width: 100%;
  height: var(--nav-height);
  padding: 0 var(--space-6);
  display: flex;
  align-items: center;
  gap: var(--space-6);
}

.navbar__brand {
  cursor: pointer;
}

.navbar__nav {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  flex: 1;
}

.nav-link {
  border: none;
  background: transparent;
  font-family: inherit;
  font-size: 14px;
  color: var(--color-text-secondary);
  padding: 8px 16px;
  border-radius: var(--radius-full);
  cursor: pointer;
  transition: all var(--transition);
  white-space: nowrap;
}

.nav-link:hover {
  color: var(--color-primary);
  background: var(--color-primary-soft);
}

.nav-link.is-active {
  color: var(--color-primary);
  background: var(--color-primary-soft);
  font-weight: 600;
}

.navbar__right {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  /* nav 在窄屏会 display:none，此时它的 flex:1 撑不开，用户框会缩回 logo 旁边。
     margin-left:auto 保证无论导航是否折叠，用户框永远贴在最右。 */
  margin-left: auto;
}

.user-chip {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 5px 12px 5px 5px;
  border-radius: var(--radius-full);
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  cursor: pointer;
  transition: all var(--transition);
  outline: none;
}

.user-chip:hover {
  border-color: var(--color-primary-light);
  box-shadow: var(--shadow-sm);
}

.avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: linear-gradient(135deg, #7da0ff, #3a63db);
  color: #fff;
  font-size: 13px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.user-chip__name {
  font-size: 13px;
  color: var(--color-text);
}

.chev {
  color: var(--color-text-muted);
}

.hamburger {
  display: none;
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  border-radius: var(--radius-sm);
  width: 38px;
  height: 38px;
  align-items: center;
  justify-content: center;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.drawer-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  padding: var(--space-2) 0;
}

.drawer-nav {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.drawer-link {
  text-align: left;
  border: none;
  background: var(--color-surface-alt);
  font-family: inherit;
  font-size: 15px;
  color: var(--color-text-secondary);
  padding: 14px 16px;
  border-radius: var(--radius-md);
  cursor: pointer;
}

.drawer-link.is-active {
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-weight: 600;
}

.drawer-logout {
  margin-top: auto;
  border: 1px solid var(--color-border);
  background: #fff;
  color: var(--color-danger);
  padding: 12px;
  border-radius: var(--radius-md);
  font-family: inherit;
  font-size: 14px;
  cursor: pointer;
}

@media (max-width: 1100px) {
  .navbar__inner {
    gap: var(--space-4);
  }

  .nav-link {
    padding: 8px 12px;
  }
}

@media (max-width: 900px) {
  .navbar__inner {
    gap: var(--space-4);
    padding: 0 var(--space-4);
  }

  .navbar__nav {
    display: none;
  }

  .hamburger {
    display: inline-flex;
  }

  .role-tag {
    display: none;
  }
}

@media (max-width: 480px) {
  .user-chip__name {
    display: none;
  }
}
</style>
