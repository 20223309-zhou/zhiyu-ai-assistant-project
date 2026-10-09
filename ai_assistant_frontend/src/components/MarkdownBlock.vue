<template>
  <div ref="root" class="md" v-html="html"></div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { hardenLinks, renderMarkdown } from '@/utils/markdown'

const props = defineProps({
  content: { type: String, default: '' }
})

const root = ref(null)
const html = computed(() => renderMarkdown(props.content))

// v-html 写入后给外链补 target/rel
function applyLinkHardening() {
  hardenLinks(root.value)
}

onMounted(() => nextTick(applyLinkHardening))
watch(html, () => nextTick(applyLinkHardening))
</script>

<style scoped>
/*
 * 样式写在 :deep() 里 —— v-html 插入的节点不带 scoped 属性，
 * 普通选择器命中不到它们。
 * 字号整体比正文略小，避免聊天气泡里出现 h1 那种夸张层级。
 */
.md {
  font-size: 14px;
  line-height: 1.75;
  color: var(--color-text);
  word-break: break-word;
}

.md :deep(> *:first-child) {
  margin-top: 0;
}

.md :deep(> *:last-child) {
  margin-bottom: 0;
}

.md :deep(p) {
  margin: 0 0 10px;
}

.md :deep(h1),
.md :deep(h2),
.md :deep(h3),
.md :deep(h4),
.md :deep(h5),
.md :deep(h6) {
  margin: 16px 0 8px;
  font-weight: 600;
  line-height: 1.4;
}

.md :deep(h1) {
  font-size: 18px;
}

.md :deep(h2) {
  font-size: 17px;
}

.md :deep(h3) {
  font-size: 15.5px;
}

.md :deep(h4),
.md :deep(h5),
.md :deep(h6) {
  font-size: 14.5px;
}

.md :deep(ul),
.md :deep(ol) {
  margin: 0 0 10px;
  padding-left: 1.5em;
}

.md :deep(li) {
  margin: 3px 0;
}

.md :deep(li > p) {
  margin: 0;
}

/* 嵌套列表收紧一点，避免层级间距过大 */
.md :deep(li > ul),
.md :deep(li > ol) {
  margin: 3px 0 0;
}

.md :deep(a) {
  color: var(--color-primary);
  text-decoration: none;
  border-bottom: 1px solid var(--color-primary-light);
}

.md :deep(a:hover) {
  color: var(--color-primary-dark);
}

.md :deep(strong) {
  font-weight: 600;
}

.md :deep(em) {
  font-style: italic;
}

.md :deep(code) {
  padding: 2px 6px;
  border-radius: 6px;
  background: var(--color-bg-deep);
  font-family: 'JetBrains Mono', Consolas, 'Courier New', monospace;
  font-size: 12.5px;
  word-break: break-all;
}

.md :deep(pre) {
  margin: 0 0 10px;
  padding: 12px 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg-deep);
  overflow-x: auto;
}

.md :deep(pre code) {
  padding: 0;
  background: transparent;
  color: var(--color-text);
  font-size: 12.5px;
  line-height: 1.65;
  word-break: normal;
  white-space: pre;
}

.md :deep(blockquote) {
  margin: 0 0 10px;
  padding: 2px 0 2px 12px;
  border-left: 3px solid var(--color-border-strong);
  color: var(--color-text-secondary);
}

.md :deep(hr) {
  margin: 14px 0;
  border: none;
  border-top: 1px solid var(--color-border);
}

.md :deep(table) {
  width: 100%;
  margin: 0 0 10px;
  border-collapse: collapse;
  font-size: 13px;
}

.md :deep(th),
.md :deep(td) {
  padding: 6px 10px;
  border: 1px solid var(--color-border);
  text-align: left;
  vertical-align: top;
}

.md :deep(th) {
  background: var(--color-bg-deep);
  font-weight: 600;
}

.md :deep(img) {
  max-width: 100%;
  border-radius: var(--radius-sm);
}
</style>
