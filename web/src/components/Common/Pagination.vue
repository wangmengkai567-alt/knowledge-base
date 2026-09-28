<script setup lang="ts">
withDefaults(defineProps<{ total: number; page: number; pageSize: number; unit?: string }>(), {
  unit: '项',
})
const emit = defineEmits<{
  (e: 'update:page', v: number): void
  (e: 'update:pageSize', v: number): void
}>()
</script>

<template>
  <div class="flex items-center justify-between px-1 py-3" v-if="total > 0">
    <span class="text-xs text-ink-400">共 {{ total }} {{ unit }}</span>
    <el-pagination
      :current-page="page"
      :page-size="pageSize"
      :total="total"
      :page-sizes="[8, 12, 20, 40]"
      layout="sizes, prev, pager, next"
      background
      @current-change="(v: number) => emit('update:page', v)"
      @size-change="(v: number) => emit('update:pageSize', v)"
    />
  </div>
</template>
