<script setup lang="ts">
import { Search } from '@element-plus/icons-vue'

withDefaults(
  defineProps<{
    title?: string
    description?: string
    icon?: any
  }>(),
  {
    title: '暂无数据',
    description: '',
    icon: Search,
  },
)
</script>

<template>
  <div class="flex flex-col items-center justify-center py-16 text-center">
    <div
      class="flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-50 to-white text-brand-500 shadow-sm ring-1 ring-brand-100/80 animate-float"
    >
      <el-icon :size="28"><component :is="icon" /></el-icon>
    </div>
    <p class="mt-4 text-sm font-semibold tracking-tight text-ink-800">{{ title }}</p>
    <p v-if="description" class="mt-1.5 max-w-sm text-[13px] leading-relaxed text-ink-400">{{ description }}</p>
    <div v-if="$slots.action" class="mt-4">
      <slot name="action" />
    </div>
  </div>
</template>
