<script setup lang="ts">
import { STAGE_LABEL, STAGE_STATUS_LABEL } from '@/types'
import { formatDate } from '@/utils/format'
import type { DocItem, IngestionStage } from '@/types'

defineProps<{ doc: DocItem; busy: boolean }>()
const emit = defineEmits<{ (e: 'retry', stage?: IngestionStage): void }>()
const stages: IngestionStage[] = ['parse', 'chunk', 'vector', 'keyword']
</script>

<template>
  <section class="liquid-card mb-5 rounded-2xl p-4">
    <div class="mb-3 flex items-center justify-between">
      <b>{{ doc.ingestionStatus === 'ready' ? '入库完成，可用于检索' : '入库尚未完成' }}</b>
      <el-button v-if="doc.ingestionStatus !== 'ready'" :loading="busy" :disabled="doc.ingestionStatus === 'processing'" @click="emit('retry')">从失败阶段继续</el-button>
    </div>
    <div class="grid gap-3 md:grid-cols-4">
      <div v-for="name in stages" :key="name" class="rounded-lg border border-ink-100 p-3">
        <p class="flex items-center justify-between text-sm">
          <span>{{ STAGE_LABEL[name] }}</span>
          <el-tag :type="doc.stages[name].status === 'failed' ? 'danger' : doc.stages[name].status === 'completed' ? 'success' : 'info'" size="small">{{ STAGE_STATUS_LABEL[doc.stages[name].status] }}</el-tag>
        </p>
        <p class="mt-2 text-xs text-ink-400">{{ formatDate(doc.stages[name].updated_at, true) }}</p>
        <p v-if="doc.stages[name].error" class="mt-2 break-words text-xs text-red-600">{{ doc.stages[name].error }}</p>
        <el-button v-if="doc.stages[name].status === 'failed'" class="mt-2" size="small" :disabled="busy || doc.ingestionStatus === 'processing'" @click="emit('retry', name)">重试{{ STAGE_LABEL[name] }}</el-button>
      </div>
    </div>
    <p v-if="doc.stages.chunk.status !== 'completed' && doc.chunkCount" class="mt-3 text-xs text-ink-500">保留的 {{ doc.chunkCount }} 个旧分块等待替换，当前不参与检索。</p>
    <p v-else class="mt-3 text-xs text-ink-500">分块 {{ doc.chunkCount }} · 向量完成 {{ doc.embeddingCompletedCount }} · 待处理 {{ doc.embeddingPendingCount }} · 失败 {{ doc.embeddingFailedCount }}</p>
  </section>
</template>
