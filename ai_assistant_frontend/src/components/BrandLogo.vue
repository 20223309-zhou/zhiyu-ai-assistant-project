<script setup>
// 品牌标识用设计稿的 logo.png（128×128，透明底）。原来是内联的渐变方块 + 星形路径，
// 换成设计稿后不要再套投影 —— 图形本身是纯色平面，加阴影只会发糊。
import logoUrl from '@/assets/logo.png'

defineProps({
  size: { type: Number, default: 36 },
  showText: { type: Boolean, default: true }
})
</script>

<template>
  <div class="brand" :style="{ '--logo-size': size + 'px' }">
    <img class="brand__mark" :src="logoUrl" alt="智语 AI" />
    <div v-if="showText" class="brand__text">
      <span class="brand__name">智语 AI</span>
      <span class="brand__slogan">企业知识库对话助手</span>
    </div>
  </div>
</template>

<style scoped>
.brand {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.brand__mark {
  width: var(--logo-size);
  height: var(--logo-size);
  flex-shrink: 0;
  display: block;
  object-fit: contain;
}

.brand__text {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
}

.brand__name {
  font-size: 17px;
  font-weight: 700;
  letter-spacing: 0.5px;
  color: var(--color-text);
}

.brand__slogan {
  font-size: 11px;
  color: var(--color-text-muted);
}

@media (max-width: 480px) {
  .brand__slogan {
    display: none;
  }
}
</style>
