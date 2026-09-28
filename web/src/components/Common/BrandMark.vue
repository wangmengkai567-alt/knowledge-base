<script setup lang="ts">
withDefaults(defineProps<{ size?: 'sm' | 'md' | 'lg' }>(), { size: 'md' })

const dim = { sm: 'h-8 w-8 text-[15px]', md: 'h-9 w-9 text-[16px]', lg: 'h-12 w-12 text-[22px]' }
</script>

<template>
  <span
    class="brand-mark inline-flex items-center justify-center rounded-[12px] font-bold tracking-tight text-white shadow-sm"
    :class="dim[size]"
    aria-hidden="true"
  >
    Q
  </span>
</template>
