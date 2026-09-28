<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{ value: number; size?: number; stroke?: number }>(),
  { size: 46, stroke: 4 },
)

const radius = computed(() => props.size / 2 - props.stroke / 2)
const circ = computed(() => 2 * Math.PI * radius.value)
const color = computed(() => {
  const v = props.value
  if (v >= 85) return '#4f46e5'
  if (v >= 65) return '#0ea5e9'
  if (v >= 45) return '#f59e0b'
  return '#94a3b8'
})
</script>

<template>
  <div
    class="relative inline-flex items-center justify-center"
    :style="{ width: size + 'px', height: size + 'px' }"
    :title="`匹配度 ${value}%`"
  >
    <svg :width="size" :height="size" class="--spin-none" style="transform: rotate(-90deg)">
      <circle
        :cx="size / 2"
        :cy="size / 2"
        :r="radius"
        fill="none"
        stroke="#e9ecf5"
        :stroke-width="stroke"
      />
      <circle
        :cx="size / 2"
        :cy="size / 2"
        :r="radius"
        fill="none"
        :stroke="color"
        :stroke-width="stroke"
        stroke-linecap="round"
        :stroke-dasharray="circ"
        :stroke-dashoffset="circ * (1 - value / 100)"
        style="transition: stroke-dashoffset 0.6s ease"
      />
    </svg>
    <div class="absolute flex flex-col items-center leading-none">
      <span class="font-bold" :style="{ color, fontSize: size * 0.26 + 'px' }">{{ value }}%</span>
    </div>
  </div>
</template>
