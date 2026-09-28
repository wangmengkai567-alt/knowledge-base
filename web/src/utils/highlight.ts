function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function escapeReg(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** 分词：按中英文标点/空白切分 */
export function tokenize(query: string): string[] {
  return query
    .split(/[\s,，。、？?！!；;：:（）()\[\]【】]+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
}

/**
 * 关键词高亮：转义后包裹 <mark class="hl">
 * 返回已转义的安全 HTML 字符串（配合 v-html 使用）
 */
export function highlight(text: string, terms: string[]): string {
  const safe = escapeHtml(text)
  const cleaned = terms.map((t) => t.trim()).filter((t) => t.length > 0)
  if (!cleaned.length) return safe
  const re = new RegExp(`(${cleaned.map(escapeReg).join('|')})`, 'gi')
  return safe.replace(re, '<mark class="hl">$1</mark>')
}
