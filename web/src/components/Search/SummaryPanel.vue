<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { Document, Refresh, ChatDotRound, CopyDocument } from '@element-plus/icons-vue'
import { searchApi } from '@/api/search'
import { useKnowledgeStore } from '@/stores/knowledge'
import { getCached, setCached } from '@/utils/searchCache'
import { renderMarkdown } from '@/utils/markdown'
import { formatDate } from '@/utils/format'
import { ElMessage } from 'element-plus'
import type { RagSource, SearchHit } from '@/types'

interface RagCache {
  answer: string
  sources: RagSource[]
  model: string
}

const RAG_TOP_K = 8

const props = defineProps<{
  query: string
  kbIds: number[]
  hits: SearchHit[]
  retrieveLoading: boolean
}>()
const router = useRouter()
const knowledge = useKnowledgeStore()

const loading = ref(false)
const answer = ref('')
const sources = ref<RagSource[]>([])
const model = ref('')
const generatedAt = ref('')
const hasGeneratedContent = ref(false)

const emit = defineEmits<{
  (e: 'update:sourceDocs', docs: RagSource[]): void
}>()

const targetKbs = computed(() => (props.kbIds.length ? props.kbIds : knowledge.kbs.map((k) => k.kbId)))
const answerHtml = computed(() => renderMarkdown(answer.value))
// 引用来源只按文档去重展示名称，不把分块正文铺到页面上。
const sourceDocs = computed(() => {
  const seen = new Set<number>()
  const list: RagSource[] = []
  for (const s of sources.value) {
    if (!s.documentId || seen.has(s.documentId)) continue
    seen.add(s.documentId)
    list.push(s)
  }
  return list
})

const phase = computed(() => {
  if (props.retrieveLoading) return 'searching'
  if (loading.value && !answer.value) return 'generating'
  if (loading.value && answer.value) return 'streaming'
  if (answer.value.includes('模型回答失败')) return 'failed'
  if (answer.value) return 'done'
  return 'idle'
})

const answerTitle = computed(() => {
  const q = props.query.replace(/[？?]+$/g, '').trim()
  return q || '根据资料生成的回答'
})
const generatedLabel = computed(() => (generatedAt.value ? formatDate(generatedAt.value, true) : ''))
const kbNameOf = (s: RagSource) => {
  const name = knowledge.getKbName(s.kbId)
  return name ? `来自${name}` : '来自知识库'
}

const lastKey = ref('')
let abort: AbortController | null = null

function cacheKey(ids: number[], chunkIds: number[]): string {
  return `rag:${props.query.trim().toLowerCase()}::${ids.join(',')}::${chunkIds.join(',')}`
}

function answerKbId(fallbackIds: number[]): number {
  const best = props.hits.slice().sort((a, b) => b.score - a.score)[0]
  if (best?.kbId) return best.kbId
  return fallbackIds[0]
}

function mergeSources(incoming: RagSource[]) {
  const scoreById = new Map(props.hits.map((h) => [h.chunkId, h.score]))
  const seen = new Set(sources.value.map((s) => s.chunkId))
  for (const s of incoming) {
    if (seen.has(s.chunkId)) continue
    seen.add(s.chunkId)
    sources.value.push({
      ...s,
      score: scoreById.get(s.chunkId) ?? s.score,
    })
  }
  sources.value = [...sources.value].sort((a, b) => b.score - a.score).slice(0, 8)
}

function chunkIdsForAll(): number[] {
  const seen = new Set<number>()
  return props.hits
    .slice()
    .sort((a, b) => b.score - a.score)
    .filter((h) => {
      if (seen.has(h.chunkId)) return false
      seen.add(h.chunkId)
      return true
    })
    .slice(0, RAG_TOP_K)
    .map((h) => h.chunkId)
}

function goSource(s: RagSource) {
  if (!s.documentId || !s.kbId) return
  router.push(`/knowledge/${s.kbId}/documents/${s.documentId}`)
}

async function copyAnswer() {
  const text = answer.value.trim()
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制回答')
  } catch {
    ElMessage.error('复制失败')
  }
}

async function streamOne(kbId: number, chunkIds: number[], kbIds: number[], signal: AbortSignal) {
  await searchApi.ragSummaryStream(
    kbId,
    props.query,
    RAG_TOP_K,
    (ev) => {
      if (ev.type === 'sources' && ev.sources) mergeSources(ev.sources)
      if (ev.type === 'chunk' && ev.delta) {
        hasGeneratedContent.value = true
        answer.value += ev.delta
        if (ev.model) model.value = ev.model
      }
      if (ev.type === 'error' && ev.error) {
        if (answer.value) answer.value += `\n\n模型回答失败`
        else answer.value = '模型回答失败'
      }
    },
    signal,
    chunkIds,
    kbIds,
  )
}

async function load(force = false) {
  if (!props.query.trim()) return
  const ids = targetKbs.value.length ? targetKbs.value : [1]
  const chunkIds = chunkIdsForAll()
  const key = cacheKey(ids, chunkIds)

  if (props.retrieveLoading) {
    abort?.abort()
    loading.value = true
    answer.value = ''
    sources.value = []
    model.value = ''
    generatedAt.value = ''
    hasGeneratedContent.value = false
    lastKey.value = ''
    return
  }

  if (!force && props.kbIds.length === 0 && knowledge.kbs.length === 0) return
  if (!force && key === lastKey.value && answer.value && !loading.value) return
  if (!force && !loading.value) {
    const cached = getCached<RagCache>(key)
    if (cached) {
      answer.value = cached.answer
      sources.value = cached.sources
      model.value = cached.model
      hasGeneratedContent.value = true
      generatedAt.value = generatedAt.value || new Date().toISOString()
      lastKey.value = key
      return
    }
  }

  abort?.abort()
  abort = new AbortController()
  const signal = abort.signal

  loading.value = true
  lastKey.value = key
  answer.value = ''
  sources.value = []
  model.value = ''
  generatedAt.value = ''
  hasGeneratedContent.value = false
  try {
    if (!chunkIds.length) {
      hasGeneratedContent.value = true
      answer.value = '我不知道'
    } else {
      const hitKbIds = [...new Set(props.hits.map((h) => h.kbId).filter((id) => id > 0))]
      const allowed = hitKbIds.length ? hitKbIds : ids
      await streamOne(answerKbId(allowed), chunkIds, allowed, signal)
    }
    if (!hasGeneratedContent.value) {
      answer.value = '模型回答失败'
    }
    if (answer.value && !loading.value) {
      generatedAt.value = new Date().toISOString()
    }
    if (
      answer.value &&
      !answer.value.includes('模型回答失败') &&
      answer.value.trim() !== '我不知道'
    ) {
      setCached(key, { answer: answer.value, sources: sources.value, model: model.value })
    }
  } catch (err: any) {
    if (err?.name === 'AbortError') return
    if (!hasGeneratedContent.value) {
      answer.value = '模型回答失败'
    } else if (err?.message) {
      answer.value += '\n\n模型回答失败'
    }
  } finally {
    if (!signal.aborted) {
      loading.value = false
      if (answer.value && !generatedAt.value) generatedAt.value = new Date().toISOString()
    }
  }
}

watch(sourceDocs, (docs) => emit('update:sourceDocs', docs), { immediate: true })
watch(
  () => [
    props.query,
    props.kbIds.join(','),
    targetKbs.value.join(','),
    props.retrieveLoading,
    props.hits.map((h) => h.chunkId).join(','),
  ],
  () => load(),
)
onMounted(load)
onUnmounted(() => abort?.abort())
</script>

<template>
  <section class="qa-card">
    <header class="qa-card__head">
      <span class="qa-card__mark">
        <el-icon><ChatDotRound /></el-icon>
      </span>
      <div class="min-w-0 flex-1">
        <h2 class="qa-card__title">{{ answerTitle }}</h2>
        <p v-if="phase === 'searching' || phase === 'generating' || phase === 'streaming'" class="qa-card__status">
          {{ phase === 'searching' ? '正在检索知识库' : phase === 'generating' ? '正在根据资料生成' : '正在生成' }}
          <span class="status-dots"><i /><i /><i /></span>
        </p>
      </div>
      <div class="qa-card__tools">
        <button class="icon-btn" type="button" :disabled="!answer" aria-label="复制回答" @click="copyAnswer">
          <el-icon><CopyDocument /></el-icon>
        </button>
        <button class="icon-btn" type="button" :disabled="loading" aria-label="重新生成" @click="load(true)">
          <el-icon :class="{ 'is-loading': loading }"><Refresh /></el-icon>
        </button>
        <time v-if="generatedLabel" class="qa-card__time">{{ generatedLabel }}</time>
      </div>
    </header>

    <div class="qa-card__body">
      <div v-if="loading && !answer" class="space-y-3">
        <div class="skeleton h-4 w-3/4" />
        <div class="skeleton h-4 w-full" />
        <div class="skeleton h-4 w-5/6" />
        <div class="skeleton h-4 w-2/3" />
      </div>

      <template v-else>
        <div class="rag-answer" v-html="answerHtml" />
      </template>
    </div>

    <footer v-if="sourceDocs.length" class="qa-card__sources">
      <button v-for="s in sourceDocs" :key="s.documentId" class="qa-source" type="button" @click="goSource(s)">
        <el-icon class="text-brand-500"><Document /></el-icon>
        <span>
          <b>{{ s.documentFilename }}</b>
          <small>{{ kbNameOf(s) }}</small>
        </span>
      </button>
    </footer>
  </section>
</template>

<style scoped>
.qa-card {
  overflow: hidden;
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.05);
}
.qa-card__head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 18px 20px 0;
}
.qa-card__mark {
  display: flex;
  height: 36px;
  width: 36px;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  border-radius: 12px;
  color: #4f46e5;
  background: #eef2ff;
  font-size: 18px;
}
.qa-card__title {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: #0f172a;
  line-height: 1.4;
}
.qa-card__status {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
  font-size: 12px;
  color: #64748b;
}
.qa-card__tools {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 4px;
  margin-left: auto;
}
.qa-card__time {
  margin-left: 6px;
  font-size: 12px;
  color: #94a3b8;
  white-space: nowrap;
}
.qa-card__body {
  padding: 14px 20px 8px;
}
.qa-card__sources {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 20px 18px;
}
.qa-source {
  display: inline-flex;
  max-width: 260px;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  background: #f8fafc;
  text-align: left;
  cursor: pointer;
  transition: border-color 160ms ease, background 160ms ease;
}
.qa-source:hover {
  border-color: #c7d2fe;
  background: #eef2ff;
}
.qa-source span {
  display: flex;
  min-width: 0;
  flex-direction: column;
}
.qa-source b {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 650;
  color: #0f172a;
}
.qa-source small {
  font-size: 11px;
  color: #94a3b8;
}
.rag-answer {
  font-size: 14px;
  line-height: 1.75;
  color: #334155;
}
.rag-answer :deep(ul) {
  list-style: disc;
  padding-left: 1.25rem;
  margin: 0.25rem 0;
}
.rag-answer :deep(ol) {
  list-style: decimal;
  padding-left: 1.25rem;
  margin: 0.25rem 0;
}
.rag-answer :deep(li) {
  margin: 0.2rem 0;
}
.rag-answer :deep(p) {
  margin: 0.4rem 0;
}
.rag-answer :deep(code) {
  background: rgb(245 245 245);
  border-radius: 4px;
  padding: 0.05rem 0.3rem;
  font-size: 0.85em;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.rag-answer :deep(strong) {
  font-weight: 600;
  color: rgb(31 41 55);
}
.rag-answer :deep(a) {
  color: rgb(79 70 229);
  text-decoration: underline;
}
@media (max-width: 640px) {
  .qa-card__time {
    display: none;
  }
}
</style>



