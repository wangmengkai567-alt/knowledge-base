<script setup lang="ts">
import { useKnowledgeStore } from '@/stores/knowledge'
import type { SearchFilters, SortOption } from '@/types'

defineProps<{ filters: SearchFilters }>()
const sort = defineModel<SortOption>('sort', { required: true })
const docScope = defineModel<'all' | 'related'>('docScope', { default: 'related' })
const knowledge = useKnowledgeStore()

const fileTypes = [
  { value: 'pdf', label: 'PDF' },
  { value: 'md', label: 'Markdown' },
  { value: 'txt', label: 'TXT' },
]
const timeRanges = [
  { value: '', label: '全部时间' },
  { value: 'day', label: '最近一天' },
  { value: 'week', label: '最近一周' },
  { value: 'month', label: '最近一月' },
  { value: 'year', label: '最近一年' },
]
const kbDots = ['#3b82f6', '#22c55e', '#f97316', '#ef4444', '#8b5cf6', '#06b6d4', '#ec4899', '#14b8a6']
const kbDot = (id: number) => kbDots[id % kbDots.length]
</script>

<template>
  <aside class="source-panel">
    <h3>数据来源</h3>

    <section>
      <el-radio-group v-model="docScope" class="source-panel__stack">
        <el-radio value="all" class="!mr-0 !h-7">
          <span>全部文档</span>
        </el-radio>
        <el-radio value="related" class="!mr-0 !h-7">
          <span>关联文档</span>
        </el-radio>
      </el-radio-group>
    </section>

    <section>
      <el-checkbox-group v-model="filters.kbIds" class="source-panel__stack">
        <el-checkbox v-for="kb in knowledge.kbs" :key="kb.kbId" :value="kb.kbId" :label="kb.kbId" class="!h-7">
          <span class="kb-dot" :style="{ background: kbDot(kb.kbId) }" />
          <span>{{ kb.name }}</span>
        </el-checkbox>
      </el-checkbox-group>
    </section>

    <section>
      <h4>文件类型</h4>
      <el-checkbox-group v-model="filters.fileTypes" class="source-panel__stack">
        <el-checkbox v-for="t in fileTypes" :key="t.value" :value="t.value" :label="t.value" class="!h-7">
          <span>{{ t.label }}</span>
        </el-checkbox>
      </el-checkbox-group>
    </section>

    <section>
      <h4>时间范围</h4>
      <el-radio-group v-model="filters.timeRange" class="source-panel__stack">
        <el-radio v-for="t in timeRanges" :key="t.value" :value="t.value" :label="t.value" class="!mr-0 !h-7">
          <span>{{ t.label }}</span>
        </el-radio>
      </el-radio-group>
    </section>

    <section>
      <h4>排序方式</h4>
      <el-radio-group v-model="sort" class="source-panel__stack">
        <el-radio value="relevance" label="relevance" class="!mr-0 !h-7">
          <span>按相关度</span>
        </el-radio>
        <el-radio value="name" label="name" class="!mr-0 !h-7">
          <span>按文件名称</span>
        </el-radio>
      </el-radio-group>
    </section>
  </aside>
</template>

<style scoped>
.source-panel {
  padding: 14px 12px 12px;
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.04);
}
.source-panel h3 {
  margin: 0 0 10px;
  font-size: 14px;
  font-weight: 700;
  color: #0f172a;
}
.source-panel section + section {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid #f1f5f9;
}
.source-panel h4 {
  margin: 0 0 8px;
  font-size: 12px;
  font-weight: 650;
  color: #64748b;
}
.source-panel__stack {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.source-panel :deep(.el-checkbox__label),
.source-panel :deep(.el-radio__label) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding-left: 6px;
  font-size: 13px;
  color: #334155;
}
.source-panel :deep(.el-checkbox.is-checked .el-checkbox__label),
.source-panel :deep(.el-radio.is-checked .el-radio__label) {
  color: #4338ca;
  font-weight: 600;
}
.source-panel :deep(.el-checkbox__inner) {
  border-radius: 4px;
}
.kb-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 999px;
  flex-shrink: 0;
}
</style>
