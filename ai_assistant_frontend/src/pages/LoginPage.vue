<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import BrandLogo from '@/components/BrandLogo.vue'
import { login, register } from '@/api'
import { useUserStore } from '@/utils/userStore'
// 自己画的抽象图案：原来是 Bing 图片搜索的直链，带水印、又是热链别人的 CDN。
// 而且这层上面盖了 78%~92% 不透明的遮罩，照片放进来只会发闷 —— 几何图案反而更透得出来。
import heroBg from '@/assets/hero-knowledge.svg'

const route = useRoute()
const router = useRouter()
const { setUser } = useUserStore()

const activeTab = ref('login')
const loading = ref(false)
const loginRef = ref()
const registerRef = ref()

const loginForm = reactive({ username: '', password: '' })
const registerForm = reactive({ name: '', username: '', password: '', confirm: '' })

const loginRules = {
  username: [{ required: true, message: '请输入登录账号', trigger: 'blur' }],
  password: [{ required: true, message: '请输入登录密码', trigger: 'blur' }]
}

// 注册只收集后端需要的字段：username（用户名，后端 username）、account（账号）、password。
// user_model.UserRegisterDto 就是这三个，多收集的字段没有地方落库。
const registerRules = {
  name: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { max: 24, message: '用户名不超过 24 个字符', trigger: 'blur' }
  ],
  username: [
    { required: true, message: '请输入账号', trigger: 'blur' },
    { min: 4, max: 16, message: '账号长度需为 4-16 个字符', trigger: 'blur' }
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 位', trigger: 'blur' }
  ],
  confirm: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    {
      validator: (_rule, value, callback) => {
        if (value !== registerForm.password) callback(new Error('两次输入的密码不一致'))
        else callback()
      },
      trigger: 'blur'
    }
  ]
}

async function submitLogin() {
  const valid = await loginRef.value?.validate().catch(() => false)
  if (!valid) return
  loading.value = true
  try {
    // 表单里的「登录账号」对应后端 account 字段
    const user = await login({ account: loginForm.username, password: loginForm.password })
    setUser(user)
    ElMessage.success(`欢迎回来，${user.name}`)
    router.push(route.query.redirect || '/chat')
  } catch (e) {
    ElMessage.error(e.message || '登录失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

async function submitRegister() {
  const valid = await registerRef.value?.validate().catch(() => false)
  if (!valid) return
  loading.value = true
  try {
    // 姓名 -> 后端 username（昵称），登录账号 -> 后端 account
    await register({
      name: registerForm.name,
      account: registerForm.username,
      password: registerForm.password
    })
    ElMessage.success('注册成功，请使用新账号登录')
    loginForm.username = registerForm.username
    loginForm.password = ''
    activeTab.value = 'login'
    Object.assign(registerForm, { name: '', username: '', password: '', confirm: '' })
  } catch (e) {
    ElMessage.error(e.message || '注册失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

function fillDemo(username, password) {
  activeTab.value = 'login'
  loginForm.username = username
  loginForm.password = password
}
</script>

<template>
  <div class="auth">
    <section class="auth__intro">
      <img class="auth__bg" :src="heroBg" alt="" aria-hidden="true" />
      <div class="auth__overlay"></div>
      <div class="auth__intro-content">
        <BrandLogo :size="44" />
        <h2>让企业知识，随问随答</h2>
        <p>上传文档即可构建知识库，问答附原文出处。</p>
      </div>
    </section>

    <section class="auth__panel">
      <div class="auth-card">
        <div class="auth-card__head">
          <h1>{{ activeTab === 'login' ? '登录智语 AI' : '创建新账号' }}</h1>
          <p>{{ activeTab === 'login' ? '使用企业账号继续，知识随问随答' : '注册后即可开始与知识库对话' }}</p>
        </div>

        <div class="auth-tabs">
          <button :class="{ 'is-active': activeTab === 'login' }" @click="activeTab = 'login'">登录</button>
          <button :class="{ 'is-active': activeTab === 'register' }" @click="activeTab = 'register'">注册</button>
        </div>

        <el-form
          v-show="activeTab === 'login'"
          ref="loginRef"
          :model="loginForm"
          :rules="loginRules"
          label-position="top"
          size="large"
          @submit.prevent="submitLogin"
        >
          <el-form-item label="登录账号" prop="username">
            <el-input v-model="loginForm.username" placeholder="请输入账号" clearable />
          </el-form-item>
          <el-form-item label="登录密码" prop="password">
            <el-input v-model="loginForm.password" type="password" placeholder="请输入密码" show-password @keyup.enter="submitLogin" />
          </el-form-item>
          <el-button type="primary" class="submit-btn" :loading="loading" @click="submitLogin">
            登录
          </el-button>
        </el-form>

        <el-form
          v-show="activeTab === 'register'"
          ref="registerRef"
          :model="registerForm"
          :rules="registerRules"
          label-position="top"
          size="large"
          @submit.prevent="submitRegister"
        >
          <el-form-item label="用户名" prop="name">
            <el-input v-model="registerForm.name" placeholder="用于展示的用户名，如「林致远」" clearable />
          </el-form-item>
          <el-form-item label="账号" prop="username">
            <el-input v-model="registerForm.username" placeholder="4-16 位字母或数字，用于登录" clearable />
          </el-form-item>
          <div class="grid-2">
            <el-form-item label="密码" prop="password">
              <el-input v-model="registerForm.password" type="password" placeholder="至少 6 位" show-password />
            </el-form-item>
            <el-form-item label="确认密码" prop="confirm">
              <el-input v-model="registerForm.confirm" type="password" placeholder="再次输入" show-password />
            </el-form-item>
          </div>
          <el-button type="primary" class="submit-btn" :loading="loading" @click="submitRegister">
            注册账号
          </el-button>
        </el-form>

        <!-- 这里的账号密码必须与数据库中的实际账号一致，改库时要同步改这里 -->
        <div class="demo-box">
          <span class="demo-box__title">演示账号</span>
          <button class="demo-chip" @click="fillDemo('admin', '123456')">管理员 admin / 123456</button>
          <button class="demo-chip" @click="fillDemo('test01', '123456')">普通用户 test01 / 123456</button>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.auth {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 1.05fr 1fr;
}

.auth__intro {
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  padding: var(--space-7);
}

.auth__bg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.auth__overlay {
  position: absolute;
  inset: 0;
  background: linear-gradient(150deg, rgba(29, 45, 92, 0.92), rgba(58, 99, 219, 0.78) 55%, rgba(34, 199, 169, 0.5));
}

.auth__intro-content {
  position: relative;
  z-index: 1;
  color: #fff;
  max-width: 460px;
}

.auth__intro-content :deep(.brand__name),
.auth__intro-content :deep(.brand__slogan) {
  color: #fff;
}

.auth__intro-content :deep(.brand__slogan) {
  opacity: 0.75;
}

.auth__intro-content h2 {
  font-size: 32px;
  line-height: 1.35;
  margin: var(--space-5) 0 var(--space-3);
}

.auth__intro-content p {
  color: rgba(255, 255, 255, 0.82);
  font-size: 14px;
  line-height: 1.8;
}

.auth__panel {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-6) var(--space-5);
  background: var(--color-bg);
}

.auth-card {
  width: 100%;
  max-width: 420px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: var(--space-6);
}

.auth-card__head h1 {
  font-size: 22px;
}

.auth-card__head p {
  color: var(--color-text-secondary);
  font-size: 13px;
  margin-top: var(--space-1);
}

.auth-tabs {
  display: flex;
  gap: var(--space-1);
  background: var(--color-bg-deep);
  border-radius: var(--radius-full);
  padding: 4px;
  margin: var(--space-5) 0;
}

.auth-tabs button {
  flex: 1;
  border: none;
  background: transparent;
  font-family: inherit;
  font-size: 14px;
  padding: 9px 0;
  border-radius: var(--radius-full);
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all var(--transition);
}

.auth-tabs button.is-active {
  background: var(--color-surface);
  color: var(--color-primary);
  font-weight: 600;
  box-shadow: var(--shadow-sm);
}

.grid-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
}

.submit-btn {
  width: 100%;
  height: 44px;
  font-size: 15px;
  margin-top: var(--space-1);
}

.demo-box {
  margin-top: var(--space-5);
  padding-top: var(--space-4);
  border-top: 1px dashed var(--color-border);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.demo-box__title {
  font-size: 12px;
  color: var(--color-text-muted);
}

.demo-chip {
  border: 1px solid var(--color-border);
  background: var(--color-surface-alt);
  border-radius: var(--radius-full);
  padding: 5px 12px;
  font-size: 12px;
  color: var(--color-text-secondary);
  font-family: inherit;
  cursor: pointer;
  transition: all var(--transition);
}

.demo-chip:hover {
  color: var(--color-primary);
  border-color: var(--color-primary-light);
  background: var(--color-primary-soft);
}

@media (max-width: 1023px) {
  .auth {
    grid-template-columns: 1fr;
  }

  .auth__intro {
    display: none;
  }
}

@media (max-width: 480px) {
  .auth-card {
    padding: var(--space-5) var(--space-4);
  }

  .grid-2 {
    grid-template-columns: 1fr;
    gap: 0;
  }
}
</style>
