<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Document, Refresh, ChatDotRound, CopyDocument } from '@element-plus/icons-vue'
import { useKnowledgeStore } from '@/stores/knowledge'
import { renderAnswer } from '@/utils/markdown'
import { formatDate } from '@/utils/format'
import { ElMessage } from 'element-plus'
import type { RagSource, RunEvent, RunStatus, RunUsage } from '@/types'

const props = defineProps<{
  query: string
  answer: string
  sources: RagSource[]
  loading: boolean
  error: string
  generatedAt: string
  runId: string
  status: RunStatus | null
  stopReason: string
  events: RunEvent[]
  usage: RunUsage | null
}>()
const emit = defineEmits<{ (e: 'regenerate'): void; (e: 'cancel'): void }>()
const router = useRouter()
const knowledge = useKnowledgeStore()
const selected = ref<RagSource | null>(null)
watch(() => props.runId, () => { selected.value = null })
const answerHtml = computed(() => renderAnswer(props.answer, props.runId, props.sources.map(s => s.citationId)))
const answerTitle = computed(() => props.query.replace(/[？?]+$/g, '').trim() || '根据资料生成的回答')
const generatedLabel = computed(() => props.generatedAt ? formatDate(props.generatedAt, true) : '')
const labels: Record<RunStatus, string> = {
  running: '正在处理', completed: '回答完成', insufficient: '资料不足',
  budget_exhausted: '已达到执行上限', repeated_call: '查询重复，已停止',
  tool_failed: '工具调用失败', model_failed: '模型调用失败', invalid_answer: '回答未通过引用检查',
  timeout: '执行超时', cancelled: '已取消',
}
const reasons: Record<string, string> = {
  model_call_limit: '已达到模型调用次数上限', tool_call_limit: '已达到工具调用次数上限',
  token_budget: '已达到累计用量上限', input_budget: '资料已达到输入上限',
  output_limit: '回答超出输出长度上限', repeated_call: '重复查询未取得新资料',
  insufficient_material: '现有资料不足以完整回答', user_cancelled: '已停止本次提问',
  invalid_citation_or_format: '回答的引用或格式不符合要求，未发布答案',
}
const statusLabel = computed(() => props.status ? labels[props.status] : '')
const noticeType = computed(() => props.status === 'cancelled' ? 'info' :
  ['insufficient', 'budget_exhausted', 'repeated_call'].includes(props.status || '') ? 'warning' : 'error')
const toolLabels: Record<string, string> = {
  knowledge_base_search: '正在查知识库', web_search: '正在搜索网页', web_read: '正在读取网页',
}
function eventLabel(event: RunEvent): string {
  if (event.kind === 'run_started') return '开始处理问题'
  if (event.kind === 'model_started') return event.data.final ? '正在整理最终回答' : '正在判断下一步'
  if (event.kind === 'tool_started') return toolLabels[String(event.data.tool)] || '正在读取资料'
  if (event.kind === 'tool_finished') return event.data.status === 'empty' ? '本次查找未取得资料' :
    ['error', 'partial'].includes(String(event.data.status)) ? '资料获取失败' : '已取得 ' + event.data.source_count + ' 份资料'
  if (event.kind === 'sources_updated') return '已准备回答依据'
  if (event.kind === 'answer_check_started') return '正在检查回答引用'
  if (event.kind === 'run_finished') return labels[event.data.status as RunStatus]
  return ''
}
const steps = computed(() => props.events.filter(event => eventLabel(event)))
const progressLabel = computed(() => steps.value.length ? eventLabel(steps.value[steps.value.length - 1]) : '正在连接')
function titleOf(source: RagSource) {
  return source.sourceType === 'knowledge_base' ? source.documentFilename : source.title
}
function descriptionOf(source: RagSource) {
  if (source.sourceType === 'knowledge_base') return '来自' + (knowledge.getKbName(source.kbId) || '知识库')
  return source.sourceType === 'web_search_summary' ? '网页搜索摘要' : '网页正文'
}
function goSource(source: RagSource) {
  if (source.sourceType !== 'knowledge_base') return
  router.push({ path: '/knowledge/' + source.kbId + '/documents/' + source.documentId,
    query: { chunkId: source.segments[0]?.chunk_id } })
}
function clickCitation(event: MouseEvent) {
  const link = (event.target as HTMLElement).closest<HTMLAnchorElement>('a[data-citation-id]')
  if (!link) return
  event.preventDefault()
  selected.value = props.sources.find(source => source.citationId === Number(link.dataset.citationId)) || null
}
async function copyAnswer() {
  if (!props.answer.trim()) return
  try { await navigator.clipboard.writeText(props.answer); ElMessage.success('已复制回答') }
  catch { ElMessage.error('复制失败') }
}
</script>

<template>
  <section class="qa-card">
    <header class="qa-card__head">
      <span class="qa-card__mark"><el-icon><ChatDotRound /></el-icon></span>
      <div class="min-w-0 flex-1">
        <h2 class="qa-card__title">{{ answerTitle }}</h2>
        <p class="qa-card__status" role="status" aria-live="polite">
          {{ loading ? progressLabel : error ? '连接失败' : statusLabel }}
          <span v-if="loading" class="status-dots"><i /><i /><i /></span>
        </p>
      </div>
      <div class="qa-card__tools">
        <el-button v-if="loading" size="small" :disabled="status !== 'running'" @click="emit('cancel')">停止</el-button>
        <button class="icon-btn" type="button" :disabled="!answer" aria-label="复制回答" @click="copyAnswer">
          <el-icon><CopyDocument /></el-icon>
        </button>
        <button class="icon-btn" type="button" :disabled="loading" aria-label="重新生成" @click="emit('regenerate')">
          <el-icon><Refresh /></el-icon>
        </button>
        <time v-if="generatedLabel" class="qa-card__time">{{ generatedLabel }}</time>
      </div>
    </header>
    <div class="qa-card__body">
      <details v-if="events.length" class="mb-3 text-xs text-ink-500">
        <summary class="cursor-pointer">执行过程 · {{ steps.length }} 步</summary>
        <ol class="mt-2 space-y-1" :aria-busy="loading">
          <li v-for="step in steps" :key="step.sequence">{{ (step.elapsed_ms / 1000).toFixed(1) }} 秒 · {{ eventLabel(step) }}</li>
        </ol>
      </details>
      <el-alert v-if="error" :title="error" type="error" :closable="false" class="mb-3" />
      <el-alert v-else-if="!loading && status && status !== 'completed'" :title="statusLabel"
        :description="reasons[stopReason] || '本次执行已结束，可重新提问。'" :type="noticeType" :closable="false" class="mb-3" />
      <div v-if="loading" class="space-y-3 py-2">
        <p class="text-xs text-ink-400">回答将在引用检查后显示</p>
        <div class="skeleton h-4 w-3/4" /><div class="skeleton h-4 w-full" />
      </div>
      <div v-if="answer" class="rag-answer" @click="clickCitation" v-html="answerHtml" />
      <p v-if="usage" class="mt-3 text-xs text-ink-400">
        模型 {{ usage.model_calls }} 次 · 工具 {{ usage.tool_calls }} 次 ·
        已报告用量 {{ usage.prompt_tokens + usage.completion_tokens }} tokens
        <span v-if="usage.unreported_model_calls"> · {{ usage.unreported_model_calls }} 次调用用量未返回</span>
      </p>
      <p v-if="runId" class="mt-1 break-all text-xs text-ink-400">运行编号：{{ runId }}</p>
    </div>
    <footer v-if="sources.length" class="qa-card__sources">
      <button v-for="source in sources" :id="'source-' + runId + '-' + source.citationId" :key="source.citationId"
        class="qa-source" type="button" @click="selected = source">
        <el-icon class="text-brand-500"><Document /></el-icon>
        <span><b>[来源 {{ source.citationId }}] {{ titleOf(source) }}</b><small>{{ descriptionOf(source) }}</small></span>
      </button>
    </footer>
    <el-dialog :model-value="!!selected" title="回答依据 · 本次实际使用的片段" width="min(720px, 92vw)" @close="selected = null">
      <template v-if="selected">
        <p class="mb-2 font-medium">[来源 {{ selected.citationId }}] {{ titleOf(selected) }}</p>
        <p class="mb-2 text-xs text-ink-500">{{ descriptionOf(selected) }}<span v-if="selected.truncated"> · 片段已截断</span></p>
        <template v-if="selected.sourceType === 'knowledge_base'">
          <p class="mb-2 text-xs text-ink-500">文档时间：{{ selected.documentUpdatedAt || '未提供' }}</p>
          <div v-for="segment in selected.segments" :key="segment.chunk_id" class="mb-3">
            <p class="text-xs text-ink-400">分块 {{ segment.position }} · 字符 {{ segment.start }}–{{ segment.end }}</p>
            <pre class="max-h-64 overflow-auto whitespace-pre-wrap text-sm leading-relaxed">{{ segment.content }}</pre>
          </div>
          <el-button link type="primary" @click="goSource(selected)">打开原文分块</el-button>
        </template>
        <template v-else>
          <p class="mb-2 text-xs text-ink-500">获取时间：{{ formatDate(selected.fetchedAt, true) }}</p>
          <pre class="max-h-80 overflow-auto whitespace-pre-wrap text-sm leading-relaxed">{{ selected.content }}</pre>
          <a v-if="/^https?:\/\//i.test(selected.url)" class="mt-3 inline-block break-all text-brand-500 underline"
            :href="selected.url" target="_blank" rel="noopener noreferrer">{{ selected.url }}</a>
        </template>
      </template>
    </el-dialog>
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



