<script setup lang="ts">
import { onMounted, ref, computed, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, Collection } from '@element-plus/icons-vue'
import { documentApi } from '@/api/document'
import DocumentPreview from '@/components/Knowledge/DocumentPreview.vue'
import { EMBEDDING_STATUS_LABEL } from '@/types'
import { renderDoc, type TocItem } from '@/utils/markdown'
import type { Chunk, DocDetail } from '@/types'

const props = defineProps<{ kbId: string; docId: string }>()
const router = useRouter()
const kbId = computed(() => Number(props.kbId))
const docId = computed(() => Number(props.docId))

const doc = ref<DocDetail | null>(null)
const chunks = ref<Chunk[]>([])
const loading = ref(false)
const selected = ref<Chunk | null>(null)

// 基于完整文档（parsed_content）整体渲染 Markdown，并抽取标题生成目录。
// 不再拼接各 chunk（切块含 overlap，拼接会产生重复 / 断行乱码）。
const rendered = computed<{ html: string; toc: TocItem[] }>(() => {
  if (!doc.value?.fullContent) return { html: '', toc: [] }
  return renderDoc(doc.value.fullContent)
})
const mergedHtml = computed(() => rendered.value.html)
const toc = computed(() => rendered.value.toc)

function embedTag(status: number) {
  if (status === 1) return 'success'
  if (status === 2) return 'danger'
  return 'info'
}

// 点击目录：正文滚动到对应标题，右侧"知识块信息"同步定位到包含该标题的 chunk
function selectHeading(item: TocItem) {
  nextTick(() => {
    document.getElementById(item.id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  })
  const hit = chunks.value.find((c) => c.content && c.content.includes(item.text.slice(0, 12)))
  if (hit) selected.value = hit
}

async function load() {
  loading.value = true
  try {
    const [docRes, chunkRes] = await Promise.all([
      documentApi.detail(kbId.value, docId.value),
      documentApi.chunks(kbId.value, docId.value, { pageSize: 200 }),
    ])
    doc.value = docRes
    // 用后端真实分块总数修正"分段数"（mapDoc 默认填 0）
    doc.value.chunkCount = chunkRes.total
    chunks.value = chunkRes.list
    selected.value = chunks.value[0] || null
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page-shell" v-loading="loading">
    <div class="mb-5 flex items-center gap-2">
      <button class="icon-btn" type="button" aria-label="返回" @click="router.push(`/knowledge/${kbId}/documents`)">
        <el-icon><ArrowLeft /></el-icon>
      </button>
      <h2 class="page-title flex min-w-0 items-center gap-2">
        <el-icon class="shrink-0 text-brand-500"><Collection /></el-icon>
        <span class="truncate">{{ doc?.title || '文档详情' }}</span>
      </h2>
    </div>

    <div class="grid grid-cols-1 gap-5 xl:grid-cols-[200px_minmax(0,1fr)_300px]">
      <aside class="liquid-card rounded-2xl p-3">
        <p class="mb-2 px-1 text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-400">
          文档目录 · {{ toc.length }}
        </p>
        <div v-if="toc.length" class="max-h-[70vh] space-y-0.5 overflow-y-auto">
          <button
            v-for="h in toc"
            :key="h.id"
            class="toc-glass block w-full truncate rounded-lg px-2 py-1.5 text-left text-[12px] text-ink-700"
            :style="{ paddingLeft: 0.5 + (h.level - 1) * 0.75 + 'rem' }"
            @click="selectHeading(h)"
          >
            {{ h.text }}
          </button>
        </div>
        <p v-else class="px-1 py-4 text-center text-xs text-ink-300">该文档无标题，暂无法生成目录</p>
      </aside>

      <DocumentPreview :doc="doc" :loading="loading" :html="mergedHtml" />

      <aside class="liquid-card rounded-2xl p-4">
        <template v-if="selected">
          <div class="mb-3 flex items-center">
            <p class="text-sm font-semibold tracking-tight text-ink-800">知识块信息</p>
          </div>
          <div class="mb-3 flex items-center justify-between text-xs">
            <span class="text-ink-400">Embedding 状态</span>
            <el-tag size="small" :type="embedTag(selected.embeddingStatus)" effect="light">
              {{ EMBEDDING_STATUS_LABEL[selected.embeddingStatus] }}
            </el-tag>
          </div>
          <p class="mb-1 text-xs font-medium text-ink-500">分块正文</p>
          <div class="max-h-[48vh] overflow-y-auto whitespace-pre-wrap rounded-lg border border-ink-100 bg-ink-50/60 p-3 text-[13px] leading-relaxed text-ink-700">
            {{ selected.content }}
          </div>
        </template>
        <p v-else class="py-10 text-center text-xs text-ink-300">选择左侧目录查看知识块</p>
      </aside>
    </div>
  </div>
</template>
