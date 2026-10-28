import { ref, reactive, watch, onUnmounted } from 'vue'
import { searchApi } from '@/api/search'
import { useKnowledgeStore } from '@/stores/knowledge'
import { useSearchHistoryStore } from '@/stores/history'
import { usePagination } from './usePagination'
import { paginateSlice } from '@/utils/format'
import { tokenize } from '@/utils/highlight'
import type { SearchHit, SearchFilters, SortOption, RagSource } from '@/types'

/** 后端一次问答产生回答、检索列表与实际送模资料。本地筛选只影响列表。 */
export function useSearch() {
  const knowledge = useKnowledgeStore()
  const history = useSearchHistoryStore()
  const loading = ref(false)
  const query = ref('')
  const hits = ref<SearchHit[]>([])
  const rawHits = ref<SearchHit[]>([])
  const allHits = ref<SearchHit[]>([])
  const answer = ref('')
  const sources = ref<RagSource[]>([])
  const model = ref('')
  const error = ref('')
  const generatedAt = ref('')
  const filters = reactive<SearchFilters>({ kbIds: [], fileTypes: [], timeRange: '' })
  const sort = ref<SortOption>('relevance')
  const { page, pageSize, total, totalPages, setTotal, setPage, changeSize, reset } = usePagination(8)
  let abort: AbortController | null = null
  let lastKey = ''
  const terms = () => tokenize(query.value)
  function refreshPage() {
    hits.value = paginateSlice(allHits.value, page.value, pageSize.value)
  }
  function recompute() {
    let list = [...rawHits.value]
    if (filters.fileTypes.length) list = list.filter((hit) => filters.fileTypes.includes(hit.fileType))
    if (filters.timeRange) {
      const days = { day: 1, week: 7, month: 30, year: 365 }[filters.timeRange]!
      const cutoff = Date.now() - days * 86400000
      list = list.filter((hit) => hit.updatedAt && new Date(hit.updatedAt).getTime() >= cutoff)
    }
    const seen = new Set<number>()
    list = list.filter((hit) => {
      if (seen.has(hit.docId)) return false
      seen.add(hit.docId)
      return true
    })
    if (sort.value === 'name') list.sort((a, b) => a.docTitle.localeCompare(b.docTitle, 'zh'))
    allHits.value = list
    setTotal(list.length)
    reset()
    refreshPage()
  }
  async function run(q?: string, force = false) {
    if (q !== undefined) query.value = q
    const text = query.value.trim()
    const kbIds = [...new Set(filters.kbIds.length ? filters.kbIds : knowledge.kbs.map((kb) => kb.kbId))].sort((a, b) => a - b)
    if (!text) return
    const key = JSON.stringify([text, kbIds])
    if (!force && key === lastKey) return
    abort?.abort()
    abort = new AbortController()
    const signal = abort.signal
    lastKey = key
    loading.value = true
    error.value = ''
    answer.value = ''
    sources.value = []
    rawHits.value = []
    model.value = ''
    generatedAt.value = ''
    recompute()
    try {
      if (!kbIds.length) throw new Error('请先创建或选择知识库')
      await searchApi.ragSummaryStream(kbIds[0], text, 8, (event) => {
        if (signal.aborted) return
        if (event.type === 'error') throw new Error(event.error)
        if (event.type === 'sources') {
          sources.value = event.sources!
          rawHits.value = event.results!
          recompute()
        }
        if (event.type === 'chunk') {
          answer.value += event.delta || ''
          model.value = event.model || ''
        }
      }, signal, kbIds)
      if (signal.aborted) return
      if (!answer.value) throw new Error('问答未完成，请重试')
      generatedAt.value = new Date().toISOString()
      history.add(text, rawHits.value.length)
    } catch (cause: unknown) {
      if (signal.aborted) return
      error.value = cause instanceof Error ? cause.message : '问答失败'
      lastKey = ''
    } finally {
      if (!signal.aborted) loading.value = false
    }
  }
  function clear() {
    abort?.abort()
    lastKey = ''
    query.value = ''
    answer.value = ''
    sources.value = []
    rawHits.value = []
    error.value = ''
    loading.value = false
    recompute()
  }
  watch(() => filters.kbIds.join(','), () => { if (query.value.trim()) run() })
  watch(() => [filters.fileTypes.join(','), filters.timeRange, sort.value], recompute)
  watch(page, refreshPage)
  watch(pageSize, refreshPage)
  onUnmounted(() => abort?.abort())
  return { loading, query, hits, rawHits, allHits, answer, sources, model, error, generatedAt,
    filters, sort, terms, page, pageSize, total, totalPages, run, clear, setPage, changeSize }
}
