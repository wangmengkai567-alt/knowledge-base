import { ref, reactive, watch, onUnmounted } from 'vue'
import { searchApi } from '@/api/search'
import { useKnowledgeStore } from '@/stores/knowledge'
import { useSearchHistoryStore } from '@/stores/history'
import { usePagination } from './usePagination'
import { paginateSlice } from '@/utils/format'
import { tokenize } from '@/utils/highlight'
import type { SearchHit, SearchFilters, SortOption, RagSource, RunEvent, RunStatus, RunUsage } from '@/types'

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
  const allowWeb = ref(false)
  const runId = ref('')
  const status = ref<RunStatus | null>(null)
  const stopReason = ref('')
  const explorationStopReason = ref('')
  const events = ref<RunEvent[]>([])
  const usage = ref<RunUsage | null>(null)
  const filters = reactive<SearchFilters>({ kbIds: [], fileTypes: [], timeRange: '' })
  const sort = ref<SortOption>('relevance')
  const { page, pageSize, total, totalPages, setTotal, setPage, changeSize, reset } = usePagination(8)
  let abort: AbortController | null = null
  let activeKey = ''
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
  function cancel() {
    if (!loading.value || status.value !== 'running') return
    abort?.abort()
    loading.value = false
    status.value = 'cancelled'
    stopReason.value = 'user_cancelled'
    activeKey = ''
  }
  async function run(q?: string) {
    if (q !== undefined) query.value = q
    const text = query.value.trim()
    const kbIds = [...new Set(filters.kbIds.length ? filters.kbIds : knowledge.kbs.map((kb) => kb.kbId))].sort((a, b) => a - b)
    if (!text) return
    const key = JSON.stringify([text, kbIds, allowWeb.value])
    if (loading.value && key === activeKey) return
    abort?.abort()
    const controller = new AbortController()
    abort = controller
    const signal = controller.signal
    activeKey = key
    loading.value = true
    error.value = ''
    answer.value = ''
    sources.value = []
    rawHits.value = []
    model.value = ''
    generatedAt.value = ''
    runId.value = ''
    status.value = 'running'
    stopReason.value = ''
    explorationStopReason.value = ''
    events.value = []
    usage.value = null
    recompute()
    try {
      if (!kbIds.length) throw new Error('请先创建或选择知识库')
      await searchApi.ragSummaryStream(kbIds[0], text, 8, (message) => {
        if (signal.aborted || abort !== controller) return
        if (message.type === 'event') {
          runId.value = message.runId
          events.value.push(message.event)
          if (message.event.kind === 'run_finished') {
            status.value = message.event.data.status as RunStatus
            stopReason.value = String(message.event.data.stop_reason)
            explorationStopReason.value = String(message.event.data.exploration_stop_reason)
          }
          if (message.sources) {
            sources.value = message.sources
            rawHits.value = message.results!
            recompute()
          }
        } else {
          const result = message.result
          runId.value = result.runId
          status.value = result.status
          stopReason.value = result.stopReason
          explorationStopReason.value = result.explorationStopReason
          answer.value = result.answer
          model.value = result.model
          sources.value = result.sources
          rawHits.value = result.results
          usage.value = result.usage
          generatedAt.value = new Date().toISOString()
          recompute()
        }
      }, signal, kbIds, allowWeb.value)
      if (signal.aborted || abort !== controller) return
      history.add(text, rawHits.value.length)
    } catch (cause: unknown) {
      if (signal.aborted || abort !== controller) return
      error.value = cause instanceof Error ? cause.message : '问答连接失败'
      answer.value = ''
      status.value = null
    } finally {
      if (abort === controller) {
        loading.value = false
        activeKey = ''
      }
    }
  }
  function clear() {
    cancel()
    abort?.abort()
    loading.value = false
    query.value = ''
    answer.value = ''
    sources.value = []
    rawHits.value = []
    error.value = ''
    runId.value = ''
    status.value = null
    stopReason.value = ''
    explorationStopReason.value = ''
    events.value = []
    usage.value = null
    recompute()
  }
  watch(() => [filters.kbIds.join(','), allowWeb.value], () => { if (query.value.trim()) run() })
  watch(() => [filters.fileTypes.join(','), filters.timeRange, sort.value], recompute)
  watch(page, refreshPage)
  watch(pageSize, refreshPage)
  onUnmounted(() => abort?.abort())
  return { loading, query, hits, rawHits, allHits, answer, sources, model, error, generatedAt,
    allowWeb, runId, status, stopReason, explorationStopReason, events, usage, cancel, filters, sort, terms, page, pageSize, total, totalPages, run, clear, setPage, changeSize }
}
