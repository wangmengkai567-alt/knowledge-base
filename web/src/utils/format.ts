/** 字符数格式化（不要当文件大小） */
export function formatCharCount(n: number): string {
  if (!n) return '0'
  if (n < 10000) return n.toLocaleString('zh-CN')
  return `${(n / 10000).toFixed(n >= 100000 ? 0 : 1)} 万`
}

/** 文件大小格式化 */
export function formatFileSize(bytes: number): string {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)))
  return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

/** 日期格式化 */
export function formatDate(iso?: string, withTime = false): string {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '-'
  const pad = (n: number) => String(n).padStart(2, '0')
  const base = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
  return withTime ? `${base} ${pad(d.getHours())}:${pad(d.getMinutes())}` : base
}

/** 相对时间 */
export function formatRelative(iso?: string): string {
  if (!iso) return '-'
  const diff = Date.now() - new Date(iso).getTime()
  const min = Math.floor(diff / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  const h = Math.floor(min / 60)
  if (h < 24) return `${h} 小时前`
  const d = Math.floor(h / 24)
  if (d < 30) return `${d} 天前`
  return formatDate(iso)
}

/** 客户端分页切片 */
export function paginateSlice<T>(list: T[], page: number, pageSize: number): T[] {
  const start = (page - 1) * pageSize
  return list.slice(start, start + pageSize)
}

/** 文件扩展名 */
export function fileExt(name: string): string {
  const m = name.match(/\.([a-z0-9]+)$/i)
  return m ? m[1].toLowerCase() : ''
}

/** 相关度分数转百分比（0~100）。后端多为 0~1，也兼容已经是百分制的值。 */
export function scorePercent(score: number): number {
  if (!Number.isFinite(score) || score <= 0) return 0
  const pct = score <= 1 ? score * 100 : score
  return Math.max(0, Math.min(100, Math.round(pct)))
}

/** 知识块摘要：去掉 Markdown 标记，优先取引用/正文，展示成通顺的一句话 */
export function formatChunkSummary(raw?: string): string {
  if (!raw) return ''
  const quoteParts = raw
    .replace(/\r\n/g, '\n')
    .split('\n')
    .map((line) => {
      const m = line.match(/^>\s*(.+)$/)
      return m ? m[1] : ''
    })
    .filter(Boolean)
  const fromInlineQuote = raw.includes('>') ? raw.split('>').pop() || raw : raw
  const source = quoteParts.length ? quoteParts.join(' ') : fromInlineQuote

  const lines = source
    .split('\n')
    .map((line) =>
      line
        .trim()
        .replace(/#{1,6}\s*/g, '')
        .replace(/^>\s*/, '')
        .replace(/^[-*+]\s+/, '')
        .replace(/^\d+[\.、)]\s+/, '')
        .replace(/[`*]+/g, '')
        .replace(/\s+/g, ' ')
        .trim(),
    )
    .filter((line) => line && !/^(-{3,}|\*{3,}|_{3,})$/.test(line))

  const substantial = lines.filter(
    (line) => line.length >= 12 || /[。！？；：]/.test(line),
  )
  let body = (substantial.length ? substantial : lines).join(' ').replace(/\s+/g, ' ').trim()
  body = body.replace(/#{1,6}\s*/g, '').replace(/\s+/g, ' ').trim()
  const cut = body.search(/[。！？；]/)
  if (cut >= 7) return body.slice(0, cut + 1)
  return body
}
