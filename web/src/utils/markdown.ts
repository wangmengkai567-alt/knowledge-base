import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

// 关闭 html：禁止解析原始 HTML 标签，避免文档内容中的脚本造成 XSS。
const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: false,
  typographer: false,
})

export interface TocItem {
  id: string
  text: string
  level: number
}

export interface RenderResult {
  html: string
  toc: TocItem[]
}

/**
 * 把完整 Markdown 文本整体渲染为 HTML，并抽取标题生成目录。
 *
 * 要点：
 * - 统一基于"完整文档"渲染（而非拼接各 chunk），避免切块 overlap 造成的重复/断行乱码。
 * - 给每个标题注入 id（sec-0、sec-1…），目录点击即可定位；id 由渲染后的真实标题顺序生成，保证一致。
 * - 最终 HTML 经 DOMPurify 清洗，阻断 <script> 等 XSS。
 */
export function renderDoc(src: string): RenderResult {
  const raw = md.render(src || '')
  const toc: TocItem[] = []

  const html = raw.replace(
    /<(h[1-6])>([\s\S]*?)<\/(h[1-6])>/g,
    (match, open: string, inner: string, close: string) => {
      // 开闭标签层级必须一致（markdown-it 保证一致，此处防御性判断）
      if (open !== close) return match
      const level = Number(open[1])
      const text = inner.replace(/<[^>]+>/g, '').trim()
      const id = `sec-${toc.length}`
      toc.push({ id, text, level })
      return `<${open} id="${id}">${inner}</${close}>`
    },
  )

  const clean = DOMPurify.sanitize(_withNoReferrer(html), {
    ADD_ATTR: ['id', 'target', 'rel', 'referrerpolicy', 'loading'],
  })
  return { html: clean, toc }
}

/** 渲染单段 Markdown（用于"知识块信息"等小块预览），经 DOMPurify 清洗防 XSS。 */
export function renderMarkdown(src: string): string {
  return DOMPurify.sanitize(_withNoReferrer(md.render(src || '')), {
    ADD_ATTR: ['id', 'target', 'rel', 'referrerpolicy', 'loading'],
  })
}

/**
 * 给所有 <img> 加上 referrerpolicy="no-referrer"，规避外链图床（CSDN 等）
 * 的 Referer 防盗链（实测带跨站 Referer 会被 403，不带则 200）。
 * 页面级 <meta name="referrer" content="no-referrer"> 已做全局保底，此处为
 * 显式双保险，也保证被复制出去的 HTML 片段同样生效。
 */
function _withNoReferrer(html: string): string {
  return html.replace(/<img(?=[\s>])/g, '<img referrerpolicy="no-referrer" loading="lazy"')
}

/** 去除 Markdown 标记，得到适合目录 / 摘要展示的纯文本。 */
export function stripMarkdown(src: string): string {
  return (src || '')
    .replace(/`([^`]+)`/g, '$1') // 行内代码
    .replace(/\*\*([^*]+)\*\*/g, '$1') // 粗体 **x**
    .replace(/__([^_]+)__/g, '$1') // 粗体 __x__
    .replace(/\*([^*]+)\*/g, '$1') // 斜体 *x*
    .replace(/_([^_]+)_/g, '$1') // 斜体 _x_
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '') // 图片
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1') // 链接
    .replace(/^#{1,6}\s+/gm, '') // 标题 #
    .replace(/^\s*[-*+]\s+/gm, '') // 无序列表
    .replace(/^\s*\d+\.\s+/gm, '') // 有序列表
    .replace(/^\s*>\s?/gm, '') // 引用
    .replace(/---+/g, ' ') // 分隔线
    .replace(/\n{2,}/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}
