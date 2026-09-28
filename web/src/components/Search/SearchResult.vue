<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { Star, Document, MoreFilled } from '@element-plus/icons-vue'
import { useFavoritesStore } from '@/stores/favorites'
import { useKnowledgeStore } from '@/stores/knowledge'
import { useHighlight } from '@/hooks/useHighlight'
import { formatChunkSummary, formatDate } from '@/utils/format'
import type { SearchHit } from '@/types'

const props = withDefaults(
  defineProps<{ hit: SearchHit; query: string; layout?: 'list' | 'grid' }>(),
  { layout: 'list' },
)
const router = useRouter()
const favorites = useFavoritesStore()
const knowledge = useKnowledgeStore()
const { highlight: hl } = useHighlight()

const faved = computed(() => favorites.has(props.hit.chunkId))
const kbName = computed(() => knowledge.getKbName(props.hit.kbId))
const displayName = computed(() => props.hit.docName || props.hit.docTitle)
const summary = computed(() => formatChunkSummary(props.hit.content) || '暂无预览')
const ext = computed(() => (props.hit.fileType || '').toUpperCase() || 'FILE')
const iconTone = computed(() => {
  const t = props.hit.fileType
  if (t === 'pdf') return 'is-pdf'
  if (t === 'docx' || t === 'doc') return 'is-doc'
  if (t === 'txt') return 'is-txt'
  return 'is-md'
})

function toggleFav(e: Event) {
  e.stopPropagation()
  favorites.toggle({
    chunkId: props.hit.chunkId,
    docId: props.hit.docId,
    kbId: props.hit.kbId,
    docName: props.hit.docName,
    docTitle: props.hit.docTitle,
    content: props.hit.content,
    score: props.hit.score,
    keyword: props.query,
  })
}

function goDetail() {
  router.push(`/knowledge/${props.hit.kbId}/documents/${props.hit.docId}`)
}

function onCommand(cmd: string) {
  if (cmd === 'open') goDetail()
}
</script>

<template>
  <article class="doc-card" :class="{ 'is-grid': layout === 'grid' }" @click="goDetail">
    <span class="doc-card__icon" :class="iconTone">
      <el-icon><Document /></el-icon>
    </span>
    <div class="doc-card__body">
      <div class="doc-card__title-row">
        <h3 class="doc-card__name" v-html="hl(displayName, query)" />
        <span class="doc-card__ext">{{ ext }}</span>
      </div>
      <p class="doc-card__meta">
        <template v-if="kbName">来自{{ kbName }}</template>
        <template v-if="hit.updatedAt">
          <i />
          {{ formatDate(hit.updatedAt) }}
        </template>
      </p>
      <p class="doc-card__summary">{{ summary }}</p>
    </div>
    <div class="doc-card__actions" @click.stop>
      <button class="icon-btn" :class="faved ? '!text-amber-500' : ''" type="button" :aria-label="faved ? '取消收藏' : '收藏'" @click="toggleFav">
        <el-icon><Star /></el-icon>
      </button>
      <el-dropdown trigger="click" @command="onCommand">
        <button class="icon-btn" type="button" aria-label="更多">
          <el-icon><MoreFilled /></el-icon>
        </button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="open">查看文档</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </article>
</template>

<style scoped>
.doc-card {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 16px;
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.92);
  cursor: pointer;
  transition:
    box-shadow 180ms ease,
    border-color 180ms ease,
    transform 180ms ease;
}
.doc-card:hover {
  border-color: rgba(165, 180, 252, 0.9);
  box-shadow: 0 10px 24px rgba(79, 70, 229, 0.08);
  transform: translateY(-1px);
}
.doc-card__icon {
  display: flex;
  height: 40px;
  width: 40px;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  border-radius: 12px;
  color: #2563eb;
  background: #eff6ff;
}
.doc-card__icon.is-pdf {
  color: #dc2626;
  background: #fef2f2;
}
.doc-card__icon.is-doc {
  color: #1d4ed8;
  background: #eff6ff;
}
.doc-card__icon.is-txt {
  color: #475569;
  background: #f8fafc;
}
.doc-card__body {
  min-width: 0;
  flex: 1;
}
.doc-card__title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.doc-card__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
  font-weight: 650;
  color: #0f172a;
}
.doc-card__ext {
  flex-shrink: 0;
  border-radius: 6px;
  padding: 1px 6px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: #2563eb;
  background: #eff6ff;
}
.doc-card__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
  font-size: 12px;
  color: #94a3b8;
}
.doc-card__meta i {
  display: inline-block;
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: #cbd5e1;
}
.doc-card__summary {
  display: -webkit-box;
  margin-top: 6px;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  font-size: 12.5px;
  line-height: 1.55;
  color: #64748b;
}
.doc-card__actions {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 2px;
}
.doc-card.is-grid {
  flex-direction: column;
  min-height: 168px;
}
.doc-card.is-grid .doc-card__actions {
  position: absolute;
  top: 10px;
  right: 10px;
}
.doc-card.is-grid {
  position: relative;
  padding-top: 16px;
}
</style>
