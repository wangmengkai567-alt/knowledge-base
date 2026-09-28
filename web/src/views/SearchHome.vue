<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import HeroDecor from '@/components/Visual/HeroDecor.vue'
import KnowledgeCard from '@/components/Knowledge/KnowledgeCard.vue'
import { useKnowledgeStore } from '@/stores/knowledge'
import { useUserStore } from '@/stores/user'
import { usePagePointer, useMagnetic } from '@/hooks/useMouseMotion'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { KnowledgeBase } from '@/types'

const router = useRouter()
const knowledge = useKnowledgeStore()
const user = useUserStore()
const { root, onMove } = usePagePointer()
const mag = useMagnetic(0.16)
const heroQuery = ref('')

const hotSearches = ['微服务架构', 'OOM 排查', 'API 网关', '数据分析', '代码评审规范', 'RAG 检索']
const displayName = computed(
  () => user.user?.nickname || user.user?.email?.split('@')[0] || 'admin',
)

onMounted(() => {
  if (knowledge.kbs.length === 0) knowledge.load()
})

function runSearch(q: string) {
  const text = q.trim()
  if (text) router.push({ path: '/search', query: { q: text } })
}

function onHeroSearch() {
  runSearch(heroQuery.value)
}

function enter(kb: KnowledgeBase) {
  router.push(`/knowledge/${kb.kbId}/documents`)
}

function openCreate() {
  router.push('/knowledge')
}

async function onDelete(kb: KnowledgeBase) {
  try {
    await ElMessageBox.confirm(
      `确认删除知识库「${kb.name}」？将同时删除其中的文档、分块和向量索引，且不可恢复。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await knowledge.remove(kb.kbId)
  ElMessage.success('已删除')
}

function onEdit() {
  router.push('/knowledge')
}
</script>

<template>
  <div ref="root" class="dash-page" @pointermove="onMove">
    <section class="hero-banner">
      <HeroDecor />
      <p class="relative text-[13px] text-white/80">你好，{{ displayName }}</p>
      <h1 class="relative mt-1 text-[1.65rem] font-semibold tracking-tight text-white sm:text-[1.9rem]">
        知识库，赋能更智能的搜索体验
      </h1>
      <p class="relative mt-2 max-w-xl text-[13px] leading-relaxed text-white/75">
        支持多种文档格式，快速构建专属知识库，让知识检索更简单。
      </p>
      <div class="hero-search relative mt-5">
        <input
          v-model="heroQuery"
          placeholder="输入关键词或自然语言问题，例如：“微服务架构的设计要点有哪些？”"
          @keyup.enter="onHeroSearch"
        />
        <button
          class="btn-shine"
          type="button"
          @click="onHeroSearch"
          @pointermove="mag.onMove"
          @pointerleave="mag.onLeave"
        >
          ✦ 开始搜索
        </button>
      </div>
      <div class="relative mt-3 flex flex-wrap items-center gap-2">
        <span class="text-[11px] text-white/60">热门搜索</span>
        <button
          v-for="h in hotSearches"
          :key="h"
          class="hero-chip"
          type="button"
          @click="runSearch(h)"
        >
          {{ h }}
        </button>
      </div>
    </section>

    <section class="mt-6">
      <div class="mb-3 flex items-center justify-between gap-3">
        <div class="flex items-center gap-2">
          <span class="h-4 w-1 rounded-full bg-brand-500" />
          <h2 class="text-[15px] font-semibold text-ink-900">知识库列表</h2>
          <span class="text-[12px] text-ink-400">共 {{ knowledge.kbs.length }} 个知识库</span>
        </div>
        <button class="dash-add" type="button" @click="openCreate">+ 新建知识库</button>
      </div>
      <div class="stagger-in grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KnowledgeCard
          v-for="(kb, i) in knowledge.kbs"
          :key="kb.kbId"
          :kb="kb"
          :index="i"
          @manage="enter"
          @view="enter"
          @edit="onEdit"
          @delete="onDelete"
        />
      </div>
    </section>

    <section class="feat-panel mt-6">
      <p class="mb-4 text-[14px] font-semibold text-ink-800">功能特性</p>
      <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div class="feat-item">
          <span>⚡</span>
          <div>
            <b>智能检索</b>
            <p>基于大模型的语义理解，精准匹配</p>
          </div>
        </div>
        <div class="feat-item">
          <span>📄</span>
          <div>
            <b>多格式支持</b>
            <p>支持 PDF、Markdown 等多种格式</p>
          </div>
        </div>
        <div class="feat-item">
          <span>🛡</span>
          <div>
            <b>知识管理</b>
            <p>灵活的权限管理与知识分类</p>
          </div>
        </div>
        <div class="feat-item">
          <span>💬</span>
          <div>
            <b>高效问答</b>
            <p>提供准确、可追溯的答案</p>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>
