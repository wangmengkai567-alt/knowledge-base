/**
 * 检索结果/摘要的轻量浏览器缓存。
 *
 * 用途：整页刷新（F5）后 Vue 应用内存状态全部清零，若每次刷新都重新发起整批
 * retrieve 再生成，极易在限流窗口内触发 429。
 * 这里用 sessionStorage 把「相同 query + 知识库范围」的结果缓存起来，刷新时直接复用，
 * 不发任何网络请求。sessionStorage 在关闭标签页时自动清空，不会留下跨会话的脏数据。
 */

const PREFIX = 'kb:cache:'
// 缓存版本号：后端重排打分逻辑/索引逻辑变更（如 reranker 分词器修正——英文词与
// 中文单字分离，使 "agent" 能命中标题 "Agent进入深水区后的十大趋势"）后，旧缓存
// 里的结果已不准确，必须失效重取。改动检索/打分/索引逻辑时请 +1。
const CACHE_VERSION = 11
// 缓存有效期：5 分钟。检索结果短时内基本不变，超期后自动失效并重新检索。
const TTL = 5 * 60 * 1000

interface Entry<T> {
  t: number
  v: T
}

export function getCached<T>(key: string): T | null {
  try {
    const raw = sessionStorage.getItem(PREFIX + `v${CACHE_VERSION}:` + key)
    if (!raw) return null
    const entry = JSON.parse(raw) as Entry<T>
    if (Date.now() - entry.t > TTL) {
      sessionStorage.removeItem(PREFIX + key)
      return null
    }
    return entry.v
  } catch {
    return null
  }
}

export function setCached<T>(key: string, value: T): void {
  try {
    sessionStorage.setItem(PREFIX + `v${CACHE_VERSION}:` + key, JSON.stringify({ t: Date.now(), v: value } as Entry<T>))
  } catch {
    // 容量溢出（如隐私模式）忽略，退化为每次实时请求
  }
}
