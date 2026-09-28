<script setup lang="ts">
import { Loading } from '@element-plus/icons-vue'

withDefaults(defineProps<{ rows?: number; showSpinner?: boolean }>(), { rows: 4, showSpinner: false })
</script>

<template>
  <div v-if="showSpinner" class="flex items-center justify-center py-20">
    <el-icon class="is-loading text-brand-500" :size="28">
      <Loading />
    </el-icon>
    <span class="ml-2 text-sm text-ink-500">检索中…</span>
  </div>
  <div v-else class="stagger-in space-y-2.5">
    <div v-for="n in rows" :key="n" class="skeleton h-14 w-full rounded-xl" />
  </div>
</template>
