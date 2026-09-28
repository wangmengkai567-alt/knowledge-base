<script setup lang="ts">
import { computed } from 'vue'
import { Cpu, Folder, Document, MoreFilled, ArrowRight } from '@element-plus/icons-vue'
import { KB_STATUS_LABEL } from '@/types'
import { usePointerGlow } from '@/hooks/usePointerGlow'
import type { KnowledgeBase } from '@/types'

const props = defineProps<{ kb: KnowledgeBase; index?: number }>()
const emit = defineEmits<{
  (e: 'view', kb: KnowledgeBase): void
  (e: 'manage', kb: KnowledgeBase): void
  (e: 'edit', kb: KnowledgeBase): void
  (e: 'delete', kb: KnowledgeBase): void
}>()

const palettes = ['violet', 'amber', 'emerald', 'cyan'] as const
const tone = computed(() => palettes[(props.index ?? props.kb.kbId) % palettes.length])

const iconMap: Record<string, any> = { cpu: Cpu, folder: Folder, document: Document, goods: Folder }
const icon = computed(() => iconMap[props.kb.icon || ''] || Document)
const { onMove, onLeave } = usePointerGlow()

function onCardClick() {
  emit('manage', props.kb)
}
</script>

<template>
  <article class="kb-tile" :data-tone="tone" @click="onCardClick" @pointermove="onMove" @pointerleave="onLeave">
    <div class="kb-tile__top">
      <span class="kb-tile__icon">
        <el-icon :size="18"><component :is="icon" /></el-icon>
      </span>
      <el-dropdown trigger="click" @command="(c: string) => emit(c as any, kb)">
        <button class="icon-btn !h-8 !w-8" type="button" aria-label="更多" @click.stop>
          <el-icon><MoreFilled /></el-icon>
        </button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="edit">编辑</el-dropdown-item>
            <el-dropdown-item command="delete" divided>删除</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>

    <h3 class="kb-tile__name">
      {{ kb.name }}
      <em v-if="index === 0">默认</em>
    </h3>
    <p class="kb-tile__desc">{{ kb.description || '暂无描述' }}</p>

    <div class="kb-tile__foot">
      <span>文档数 {{ kb.docCount }}</span>
      <span>{{ kb.chunkCount }} 条结果</span>
      <span class="kb-tile__go" :title="KB_STATUS_LABEL[kb.status]">
        <el-icon><ArrowRight /></el-icon>
      </span>
    </div>
  </article>
</template>
