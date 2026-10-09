import { parse, compileTemplate } from '@vue/compiler-sfc'
import fs from 'node:fs'

const file = 'src/pages/MyKnowledgePage.vue'
const { descriptor } = parse(fs.readFileSync(file, 'utf-8'), { filename: file })
const res = compileTemplate({ source: descriptor.template.content, filename: file, id: 'x' })
const code = res.code
const i = code.indexOf('_createElementVNode("template"')
console.log('=== 裸 template 的渲染上下文 ===')
console.log(code.slice(i, i + 420))
console.log()
console.log('=== 该 template 之后是否紧跟 kb-card 内容 ===')
const j = code.indexOf('privateUploadRef', i)
console.log('privateUploadRef 位置:', j, '| template 位置:', i, '| 包含在 template 内:', j > i && j < i + 3000)
