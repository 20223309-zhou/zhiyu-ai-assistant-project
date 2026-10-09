/**
 * 模型回复的 Markdown 渲染。
 *
 * 为什么不直接把原文塞进 v-html：
 * 回复内容来自大模型，也可能间接带上知识库正文，属于不可信输入。
 * 所以先用 marked 解析，再用 DOMPurify 白名单净化，最后才交给 v-html。
 * 千万不要绕过 sanitize —— 那等于把 XSS 入口开在聊天框上。
 */

import DOMPurify from 'dompurify'
import { marked } from 'marked'

marked.setOptions({
  gfm: true,
  // 模型回复常按「一句话一行」写，单换行当成换行更符合预期，否则会被拼成一段
  breaks: true
})

/**
 * Markdown 文本 -> 已净化的 HTML 字符串。
 * @param {string} text
 * @returns {string} 可直接用于 v-html 的 HTML
 */
export function renderMarkdown(text) {
  if (!text) return ''
  const raw = marked.parse(String(text), { async: false })
  return DOMPurify.sanitize(raw, {
    // 只允许常规 HTML 标签，不放行 SVG / MathML，缩小攻击面
    USE_PROFILES: { html: true },
    // 外链一律带 noopener，避免 window.opener 被利用
    ADD_ATTR: ['target', 'rel']
  })
}

/**
 * 给净化结果里的外链补上 target/rel。
 * DOMPurify 允许 target/rel 属性，但不会主动添加，这里统一处理。
 * @param {HTMLElement | null} root 渲染容器
 */
export function hardenLinks(root) {
  if (!root) return
  root.querySelectorAll('a[href]').forEach((a) => {
    a.setAttribute('target', '_blank')
    a.setAttribute('rel', 'noopener noreferrer')
  })
}
