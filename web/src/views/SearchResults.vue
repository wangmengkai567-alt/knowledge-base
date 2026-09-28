<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ChatDotRound, FolderOpened, Grid, List } from '@element-plus/icons-vue'
import { useSearch } from '@/hooks/useSearch'
import { useKnowledgeStore } from '@/stores/knowledge'
import { useUserStore } from '@/stores/user'
import FilterPanel from '@/components/Search/FilterPanel.vue'
import SearchResult from '@/components/Search/SearchResult.vue'
import SummaryPanel from '@/components/Search/SummaryPanel.vue'
import Pagination from '@/components/Common/Pagination.vue'
import Loading from '@/components/Common/Loading.vue'
import EmptyState from '@/components/Common/EmptyState.vue'
import { paginateSlice } from '@/utils/format'
import type { RagSource } from '@/types'

defineOptions({ name: 'SearchResults' })

const route = useRoute()
const router = useRouter()
const knowledge = useKnowledgeStore()
const user = useUserStore()
const { loading, query, rawHits, allHits, failedKbCount, filters, sort, page, pageSize, run, setPage, changeSize } =
  useSearch()

const filterOpen = ref(false)
const docScope = ref<'all' | 'related'>('related')
const viewMode = ref<'list' | 'grid'>('list')
const sourceDocs = ref<RagSource[]>([])

onMounted(async () => {
  if (knowledge.kbs.length === 0) await knowledge.load()
  initFromRoute()
})

function initFromRoute() {
  const q = (route.query.q as string) || ''
  const kbIds = route.query.kbIds ? String(route.query.kbIds).split(',').map(Number) : []
  if (kbIds.length) filters.kbIds = kbIds
  if (q) run(q)
}

watch(
  () => route.query.q,
  (q) => {
    if (q) run(String(q))
  },
)

watch(docScope, () => setPage(1))

const relatedIds = computed(() => new Set(sourceDocs.value.map((s) => s.documentId).filter(Boolean)))
const scopedList = computed(() => {
  if (docScope.value !== 'related') return allHits.value
  if (!relatedIds.value.size) return allHits.value
  return allHits.value.filter((h) => relatedIds.value.has(h.docId))
})
const scopedTotal = computed(() => scopedList.value.length)
const scopedHits = computed(() => paginateSlice(scopedList.value, page.value, pageSize.value))

watch(relatedIds, () => {
  if (docScope.value === 'related') setPage(1)
})
const displayName = computed(
  () => user.user?.nickname || user.user?.email?.split('@')[0] || '我',
)
const initial = computed(() => displayName.value?.trim()?.[0]?.toUpperCase() || 'U')

function onSourceDocs(docs: RagSource[]) {
  sourceDocs.value = docs
}
</script>

<template>
  <div class="page-shell qa-page">
    <div class="qa-page__head">
      <div class="qa-page__title">
        <span class="qa-page__mark">
          <el-icon><ChatDotRound /></el-icon>
        </span>
        <div>
          <h1>智能问答</h1>
          <p>基于你的知识库，快速找到你需要的答案</p>
        </div>
      </div>
      <button
        v-if="query"
        class="qa-page__query"
        type="button"
        :title="query"
        @click="router.push({ path: '/search', query: { q: query } })"
      >
        {{ query }}
        <span class="qa-page__avatar" :title="displayName">{{ initial }}</span>
      </button>
    </div>

    <EmptyState
      v-if="!query"
      title="输入问题开始提问"
      description="先检索知识库资料，再由模型根据资料生成回答"
    />

    <div v-else class="qa-page__stack">
      <SummaryPanel
        :query="query"
        :kbIds="filters.kbIds"
        :hits="rawHits"
        :retrieve-loading="loading"
        @update:source-docs="onSourceDocs"
      />

      <div class="qa-page__split">
        <div>
          <button class="qa-page__filter-toggle" type="button" @click="filterOpen = !filterOpen">
            {{ filterOpen ? '收起筛选' : '数据来源' }}
          </button>
          <div :class="filterOpen ? 'block' : 'hidden lg:block'">
            <FilterPanel :filters="filters" v-model:sort="sort" v-model:doc-scope="docScope" />
          </div>
        </div>

        <section class="related-panel">
          <div class="related-head">
            <p>
              <el-icon class="related-head__icon"><FolderOpened /></el-icon>
              相关文档 <b>{{ scopedTotal }}</b> 份
            </p>
            <div class="view-toggle" role="group" aria-label="视图切换">
              <button type="button" :class="{ 'is-active': viewMode === 'list' }" @click="viewMode = 'list'">
                <el-icon><List /></el-icon>
                列表
              </button>
              <button type="button" :class="{ 'is-active': viewMode === 'grid' }" @click="viewMode = 'grid'">
                <el-icon><Grid /></el-icon>
                网格
              </button>
            </div>
          </div>

          <el-alert
            v-if="failedKbCount && !loading"
            class="mb-3"
            type="warning"
            :closable="false"
            :title="
              scopedHits.length
                ? `${failedKbCount} 个知识库检索失败，已展示其余结果`
                : '检索失败，请稍后重试或检查服务是否可用'
            "
          />

          <Loading v-if="loading" :rows="4" />
          <EmptyState
            v-else-if="!scopedHits.length"
            :title="failedKbCount ? '检索失败' : docScope === 'related' ? '暂无关联文档' : '未找到相关资料'"
            :description="
              failedKbCount
                ? '请稍后重试，或缩小知识库范围后再搜'
                : docScope === 'related'
                  ? '回答尚未引用文档，或可改回「全部文档」查看检索结果'
                  : '没有可依据的资料时，模型无法按知识库作答。可换关键词或换一个知识库。'
            "
          />
          <div v-else class="related-list" :class="{ 'is-grid': viewMode === 'grid' }">
            <SearchResult v-for="h in scopedHits" :key="h.docId" :hit="h" :query="query" :layout="viewMode" />
          </div>
          <Pagination
            v-if="!loading && scopedHits.length"
            class="related-pager"
            unit="份"
            :total="scopedTotal"
            :page="page"
            :pageSize="pageSize"
            @update:page="setPage"
            @update:pageSize="changeSize"
          />
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.qa-page__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}
.qa-page__title {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  min-width: 0;
}
.qa-page__mark {
  display: flex;
  height: 40px;
  width: 40px;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  border-radius: 14px;
  color: #4f46e5;
  background: #eef2ff;
  font-size: 20px;
}
.qa-page__head h1 {
  margin: 0;
  font-size: 22px;
  font-weight: 750;
  letter-spacing: -0.03em;
  color: #0f172a;
}
.qa-page__head p {
  margin: 4px 0 0;
  font-size: 13px;
  color: #64748b;
}
.qa-page__query {
  display: inline-flex;
  max-width: min(46vw, 460px);
  align-items: center;
  gap: 10px;
  overflow: hidden;
  padding: 0;
  border: 0;
  background: transparent;
  color: #4f46e5;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}
.qa-page__query:hover {
  text-decoration: underline;
}
.qa-page__avatar {
  display: inline-flex;
  height: 28px;
  width: 28px;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  background: linear-gradient(180deg, #818cf8, #4f46e5);
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  text-decoration: none;
}
.qa-page__stack {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.qa-page__split {
  display: grid;
  grid-template-columns: 1fr;
  gap: 18px;
}
.qa-page__filter-toggle {
  display: inline-flex;
  margin-bottom: 10px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.8);
  padding: 6px 10px;
  font-size: 12px;
  color: #475569;
}
.related-panel {
  min-width: 0;
  padding: 16px 18px 10px;
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.04);
}
.related-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}
.related-head p {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  font-size: 14px;
  color: #475569;
}
.related-head__icon {
  color: #64748b;
  font-size: 16px;
}
.related-head b {
  color: #0f172a;
  font-weight: 700;
}
.view-toggle {
  display: inline-flex;
  gap: 8px;
}
.view-toggle button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #fff;
  padding: 6px 12px;
  font-size: 12px;
  color: #64748b;
  cursor: pointer;
}
.view-toggle button.is-active {
  color: #fff;
  background: #4f46e5;
  border-color: #4f46e5;
  font-weight: 650;
  box-shadow: 0 8px 16px rgba(79, 70, 229, 0.22);
}
.related-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.related-list.is-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
}
.related-pager {
  margin-top: 4px;
}
@media (min-width: 1024px) {
  .qa-page__split {
    grid-template-columns: 228px minmax(0, 1fr);
    align-items: start;
  }
  .qa-page__filter-toggle {
    display: none;
  }
  .related-list.is-grid {
    grid-template-columns: 1fr 1fr;
  }
}
@media (max-width: 640px) {
  .qa-page__query {
    display: none;
  }
}
</style>
