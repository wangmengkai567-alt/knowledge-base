<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useFavoritesStore } from '@/stores/favorites'
import { Star, Delete, Document } from '@element-plus/icons-vue'
import { scorePercent, formatDate } from '@/utils/format'
import EmptyState from '@/components/Common/EmptyState.vue'
import PageHeader from '@/components/Common/PageHeader.vue'
import ScoreRing from '@/components/Common/ScoreRing.vue'

const router = useRouter()
const fav = useFavoritesStore()

function go(kbId: number, docId: number) {
  router.push(`/knowledge/${kbId}/documents/${docId}`)
}
</script>

<template>
  <div class="page-shell mx-auto max-w-4xl">
    <PageHeader title="我的收藏" subtitle="收藏的知识片段，便于后续快速查阅" />

    <EmptyState
      v-if="!fav.items.length"
      :icon="Star"
      title="暂无收藏"
      description="在检索结果中点击星标即可将文档保存到这里"
    />

    <div v-else class="stagger-in space-y-2.5">
      <div
        v-for="f in fav.items"
        :key="f.chunkId"
        class="liquid-card liquid-card-interactive flex items-start gap-3 rounded-xl p-4"
      >
        <ScoreRing :value="scorePercent(f.score)" :size="42" />
        <div class="min-w-0 flex-1 cursor-pointer" @click="go(f.kbId, f.docId)">
          <div class="flex items-center gap-1.5">
            <el-icon class="text-brand-500"><Document /></el-icon>
            <span class="truncate text-sm font-semibold text-ink-900">{{ f.docTitle }}</span>
          </div>
          <p class="mt-1 line-clamp-2 text-[13px] leading-relaxed text-ink-600">{{ f.content }}</p>
          <p class="mt-1 text-[11px] text-ink-300">收藏于 {{ formatDate(f.savedAt, true) }}</p>
        </div>
        <el-button text type="danger" :icon="Delete" @click="fav.remove(f.chunkId)" />
      </div>
    </div>
  </div>
</template>
