<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { Search, Setting, Close } from '@element-plus/icons-vue'
import { useKnowledgeStore } from '@/stores/knowledge'

const props = withDefaults(
  defineProps<{
    modelValue?: string
    placeholder?: string
    size?: 'lg' | 'md'
    showAdvanced?: boolean
  }>(),
  {
    modelValue: '',
    placeholder: '输入关键词或自然语言问题，例如：微服务架构如何设计？',
    size: 'lg',
    showAdvanced: true,
  },
)
const emit = defineEmits<{
  (e: 'update:modelValue', v: string): void
  (e: 'search', payload: { query: string; kbIds: number[] }): void
}>()

const knowledge = useKnowledgeStore()
const text = ref(props.modelValue)
const advancedKbIds = ref<number[]>([])
const advancedOpen = ref(false)

watch(
  () => props.modelValue,
  (v) => {
    if (v !== text.value) text.value = v || ''
  },
)

onMounted(() => {
  if (knowledge.kbs.length === 0) knowledge.load()
})

function sync(v: string) {
  text.value = v
  emit('update:modelValue', v)
}

function onSearch() {
  const q = text.value.trim()
  if (!q) return
  emit('update:modelValue', q)
  emit('search', { query: q, kbIds: [...advancedKbIds.value] })
}
</script>

<template>
  <div :class="size === 'lg' ? 'w-full max-w-3xl' : 'w-full'">
    <div class="search-composer" :class="{ 'is-md': size === 'md' }">
      <el-icon class="shrink-0 text-ink-400"><Search /></el-icon>
      <input
        class="search-composer__input"
        :value="text"
        :placeholder="placeholder"
        @input="sync(($event.target as HTMLInputElement).value)"
        @keyup.enter="onSearch"
      />
      <button
        v-if="text"
        class="icon-btn !h-8 !w-8 text-ink-300"
        type="button"
        aria-label="清空"
        @click="sync('')"
      >
        <el-icon><Close /></el-icon>
      </button>
      <button class="search-composer__submit" type="button" @click="onSearch">
        {{ size === 'lg' ? '检索' : '提问' }}
      </button>
    </div>

    <div v-if="showAdvanced" class="mt-2.5 flex items-center gap-3 text-xs text-ink-400">
      <el-popover v-model:visible="advancedOpen" placement="bottom-start" :width="280" trigger="click">
        <template #reference>
          <button class="inline-flex items-center gap-1 transition hover:text-brand-600" type="button">
            <el-icon><Setting /></el-icon> 高级搜索
          </button>
        </template>
        <div class="space-y-3">
          <div>
            <p class="mb-2 text-xs font-medium text-ink-600">检索范围（知识库）</p>
            <el-checkbox-group v-model="advancedKbIds" class="flex flex-col">
              <el-checkbox v-for="kb in knowledge.kbs" :key="kb.kbId" :value="kb.kbId" :label="kb.kbId">
                {{ kb.name }}
              </el-checkbox>
            </el-checkbox-group>
          </div>
          <p class="text-[11px] leading-relaxed text-ink-400">
            不选则检索全部知识库。更多筛选（文件类型、时间范围、排序）可在结果页调整。
          </p>
        </div>
      </el-popover>
      <span class="hidden sm:inline">支持关键词 / 自然语言提问</span>
      <span
        class="ml-auto hidden rounded-md border border-ink-200/80 bg-white/50 px-1.5 py-0.5 font-mono text-[10px] text-ink-400 sm:inline"
      >
        Enter
      </span>
    </div>
  </div>
</template>
