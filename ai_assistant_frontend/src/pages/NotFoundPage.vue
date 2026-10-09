<script setup>
import { useRouter } from 'vue-router'
// 自己画的示意图，替换原来爬来的带水印外链图
import notFoundImg from '@/assets/not-found.svg'

const router = useRouter()
</script>

<template>
  <div class="nf">
    <img class="nf__img" :src="notFoundImg" alt="" aria-hidden="true" />
    <h1>404</h1>
    <p>你访问的页面不存在，或已被移动到其他位置。</p>
    <el-button type="primary" size="large" @click="router.push('/chat')">返回 AI 对话</el-button>
  </div>
</template>

<style scoped>
.nf {
  max-width: 560px;
  margin: 0 auto;
  padding: var(--space-7) var(--space-5);
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-4);
}

/* 透明底插画：contain 不裁图案，去掉圆角与投影免得像贴图 */
.nf__img {
  width: 100%;
  height: 220px;
  object-fit: contain;
}

.nf h1 {
  font-size: 40px;
  color: var(--color-primary);
  letter-spacing: 2px;
}

.nf p {
  color: var(--color-text-secondary);
  font-size: 14px;
}

@media (max-width: 767px) {
  .nf {
    padding: var(--space-5) var(--space-4);
  }

  .nf__img {
    height: 160px;
  }
}
</style>
