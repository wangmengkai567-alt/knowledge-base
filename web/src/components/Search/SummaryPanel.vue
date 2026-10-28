<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { Document, Refresh, ChatDotRound, CopyDocument } from '@element-plus/icons-vue'
import { useKnowledgeStore } from '@/stores/knowledge'
import { renderMarkdown } from '@/utils/markdown'
import { formatDate } from '@/utils/format'
import { ElMessage } from 'element-plus'
import type { RagSource } from '@/types'

const props = defineProps<{
  query: string
  answer: string
  sources: RagSource[]
  loading: boolean
  error: string
  generatedAt: string
}>()
const emit = defineEmits<{ (e: 'regenerate'): void }>()
const router = useRouter()
const knowledge = useKnowledgeStore()
const answerHtml = computed(() => renderMarkdown(props.answer))
const sourceDocs = computed(() => props.sources)
const phase = computed(() => props.loading ? 'generating' : props.error ? 'failed' : props.answer ? 'done' : 'idle')
const answerTitle = computed(() => props.query.replace(/[？?]+$/g, '').trim() || '根据资料生成的回答')
const generatedLabel = computed(() => props.generatedAt ? formatDate(props.generatedAt, true) : '')
const kbNameOf = (source: RagSource) => {
  const name = knowledge.getKbName(source.kbId)
  return name ? '来自' + name : '来自知识库'
}
function goSource(source: RagSource) {
  router.push('/knowledge/' + source.kbId + '/documents/' + source.documentId)
}
async function copyAnswer() {
  if (!props.answer.trim()) return
  try {
    await navigator.clipboard.writeText(props.answer)
    ElMessage.success('已复制回答')
  } catch {
    ElMessage.error('复制失败')
  }
}
</script>

<template>
  <section class="qa-card">
    <header class="qa-card__head">
      <span class="qa-card__mark">
        <el-icon><ChatDotRound /></el-icon>
      </span>
      <div class="min-w-0 flex-1">
        <h2 class="qa-card__title">{{ answerTitle }}</h2>
        <p v-if="phase === 'generating'" class="qa-card__status">
          正在检索并根据资料生成
          <span class="status-dots"><i /><i /><i /></span>
        </p>
      </div>
      <div class="qa-card__tools">
        <button class="icon-btn" type="button" :disabled="!answer" aria-label="复制回答" @click="copyAnswer">
          <el-icon><CopyDocument /></el-icon>
        </button>
        <button class="icon-btn" type="button" :disabled="loading" aria-label="重新生成" @click="emit('regenerate')">
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

      <el-alert v-else-if="error" :title="error" type="error" :closable="false" />
      <template v-else>
        <div class="rag-answer" v-html="answerHtml" />
      </template>
    </div>

    <footer v-if="sourceDocs.length" class="qa-card__sources">
      <el-popover v-for="s in sourceDocs" :key="s.citationId" trigger="click" placement="top" :width="480">
        <template #reference>
          <button class="qa-source" type="button">
            <el-icon class="text-brand-500"><Document /></el-icon>
            <span>
              <b>[来源 {{ s.citationId }}] {{ s.documentFilename }}</b>
              <small>{{ kbNameOf(s) }}</small>
            </span>
          </button>
        </template>
        <p class="mb-2 font-medium">[来源 {{ s.citationId }}] {{ s.documentFilename }}</p>
        <pre class="max-h-80 overflow-auto whitespace-pre-wrap text-xs leading-relaxed">{{ s.content }}</pre>
        <el-button class="mt-2" link type="primary" @click="goSource(s)">打开原文</el-button>
      </el-popover>
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



