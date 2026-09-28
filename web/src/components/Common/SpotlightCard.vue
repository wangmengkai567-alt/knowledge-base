<script setup lang="ts">
import { usePointerGlow } from '@/hooks/usePointerGlow'

withDefaults(
  defineProps<{
    as?: string
    tilt?: boolean
    interactive?: boolean
  }>(),
  { as: 'div', tilt: false, interactive: true },
)

const { onMove, onLeave } = usePointerGlow()
</script>

<template>
  <component
    :is="as"
    class="spotlight-card"
    :class="{ 'is-tilt': tilt, 'is-interactive': interactive }"
    @pointermove="onMove"
    @pointerleave="onLeave"
  >
    <slot />
  </component>
</template>
