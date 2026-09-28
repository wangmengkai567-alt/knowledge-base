import { ref, reactive, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { searchApi } from '@/api/search'
import { useKnowledgeStore } from '@/stores/knowledge'
import { useSearchHistoryStore } from '@/stores/history'
import { usePagination } from './usePagination'
import { paginateSlice } from '@/utils/format'
import { tokenize } from '@/utils/highlight'
import { getCached, setCached } from '@/utils/searchCache'
import type { SearchHit, SearchFilters, SortOption } from '@/types'

/**
 * 检索核心逻辑：
 * - 支持跨知识库检索（merge 多个知识库的 retrieve 结果）
 * - 支持文件类型 / 时间范围本地筛选
 * - 支持按相关度 / 名称排序
 * - 客户端分页
 * - 自动写入搜索历史
 */
export function useSearch() {
  const knowledge = useKnowledgeStore()
  const history = useSearchHistoryStore()

  const loading = ref(false)
  const query = ref('')
  const hits = ref<SearchHit[]>([])
  const failedKbCount = ref(0)
  const filters = reactive<SearchFilters>({ kbIds: [], fileTypes: [], timeRange: '' })
  const sort = ref<SortOption>('relevance')

  const { page, pageSize, total, totalPages, setTotal, setPage, changeSize, reset } = usePagination(8)

  const rawHits = ref<SearchHit[]>([])
  const allHits = ref<SearchHit[]>([])

  // 上次实际发起检索的 key（query + 知识库范围），用于防止重复发起整批 heavy 请求
  const lastRunKey = ref('')

  const terms = () => tokenize(query.value)

  function targetKbIds(): number[] {
    return filters.kbIds.length ? filters.kbIds : knowledge.kbs.map((k) => k.kbId)
  }

  // 用路由控制的知识库选择（filters.kbIds）做 key，而非解析后的全部知识库。
  // 因为 filters.kbIds 随 URL 在刷新后恢复，可保证刷新时 key 稳定、能命中缓存；
  // 而解析后的全量知识库依赖 store 异步加载，刷新瞬间可能为空导致 key 失稳。
  // 关键修复：key 对 query 做大小写归一化——"docker" 与 "Docker" 视为同一检索，
  // 否则二者会被缓存成两条独立记录，任一时段缓存了空结果就会在会话内"翻转"
  // （搜 docker 有结果、搜 Docker 无结果），与实际后端大小写无关的行为不一致。
  function runKey(text: string): string {
    return `${text.trim().toLowerCase()}::${filters.kbIds.join(',')}`
  }

  function refreshPage() {
    hits.value = paginateSlice(allHits.value, page.value, pageSize.value)
  }

  function recompute() {
    let list = [...rawHits.value]
    if (filters.fileTypes.length) {
      list = list.filter((h) => filters.fileTypes.includes(h.fileType))
    }
    if (filters.timeRange) {
      const days = { day: 1, week: 7, month: 30, year: 365 }[filters.timeRange]!
      const cutoff = Date.now() - days * 86400000
      list = list.filter((h) => h.updatedAt && new Date(h.updatedAt).getTime() >= cutoff)
    }
    const best = new Map<number, SearchHit>()
    for (const h of list) {
      const prev = best.get(h.docId)
      if (!prev || h.score > prev.score) best.set(h.docId, h)
    }
    list = Array.from(best.values())
    if (sort.value === 'name') {
      list.sort((a, b) => a.docTitle.localeCompare(b.docTitle, 'zh'))
    } else {
      list.sort((a, b) => b.score - a.score)
    }
    allHits.value = list
    setTotal(list.length)
    reset()
    refreshPage()
  }

  async function run(q?: string) {
    if (q !== undefined) query.value = q
    const text = query.value.trim()
    if (!text) return
    // 防重复：相同 query + 相同知识库范围，且已有结果、非加载中 → 直接复用，
    // 避免返回上一页/重复触发导致整批 retrieve 重发，触发 429 限流。
    const key = runKey(text)
    if (!loading.value && key === lastRunKey.value && rawHits.value.length > 0) return
    // 整页刷新（F5）后内存状态清零，内存去重失效；此处用 sessionStorage 恢复结果，
    // 刷新时直接复用、不发任何网络请求，从根本上消除刷新触发的 429。
    if (!loading.value) {
      const cached = getCached<SearchHit[]>(`search:${key}`)
      if (cached) {
        rawHits.value = cached
        lastRunKey.value = key
        failedKbCount.value = 0
        recompute()
        return
      }
    }
    loading.value = true
    lastRunKey.value = key
    try {
      const kbIds = targetKbIds()
      const failures: number[] = []
      const calls = kbIds.map((id) =>
        searchApi.retrieve(id, text, 30).then((r) => r.results).catch(() => {
          failures.push(id)
          return [] as SearchHit[]
        }),
      )
      const merged = (await Promise.all(calls)).flat()
      failedKbCount.value = failures.length
      if (failures.length && failures.length < kbIds.length) {
        ElMessage.warning(`${failures.length} 个知识库检索失败，已展示其余结果`)
      }
      const seen = new Set<number>()
      rawHits.value = merged.filter((h) => {
        if (seen.has(h.chunkId)) return false
        seen.add(h.chunkId)
        return true
      })
      recompute()
      history.add(text, rawHits.value.length)
      // 只缓存非空结果：空结果不写缓存，避免陈旧"空结果"在会话内被反复复用，
      // 导致后端已能返回结果时仍显示"未找到相关结果"。
      if (rawHits.value.length > 0) {
        setCached(`search:${key}`, rawHits.value)
      }
    } finally {
      loading.value = false
    }
  }

  function clear() {
    query.value = ''
    rawHits.value = []
    allHits.value = []
    hits.value = []
    failedKbCount.value = 0
    setTotal(0)
    reset()
  }

  // 筛选/排序/分页变化联动
  watch(
    () => filters.fileTypes.join(','),
    () => {
      if (rawHits.value.length) recompute()
    },
  )
  watch(
    () => filters.timeRange,
    () => {
      if (rawHits.value.length) recompute()
    },
  )
  watch(
    () => filters.kbIds.join(','),
    () => {
      if (query.value.trim()) run()
    },
  )
  watch(sort, () => {
    if (rawHits.value.length) recompute()
  })
  watch(page, refreshPage)
  watch(pageSize, refreshPage)

  return {
    loading,
    query,
    hits,
    rawHits,
    allHits,
    failedKbCount,
    filters,
    sort,
    terms,
    page,
    pageSize,
    total,
    totalPages,
    run,
    clear,
    setPage,
    changeSize,
  }
}
