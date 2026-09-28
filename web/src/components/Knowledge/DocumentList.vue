<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Delete, View, Download, Refresh, MoreFilled } from '@element-plus/icons-vue'
import { formatDate, formatFileSize, paginateSlice } from '@/utils/format'
import { PARSE_STATUS_LABEL, INDEX_STATUS_LABEL, ParseStatus, IndexStatus } from '@/types'
import { usePointerGlow } from '@/hooks/usePointerGlow'
import type { DocItem } from '@/types'

const props = defineProps<{ docs: DocItem[]; loading?: boolean; kbId: number }>()
const emit = defineEmits<{
  (e: 'view', doc: DocItem): void
  (e: 'delete', doc: DocItem): void
  (e: 'delete-many', docs: DocItem[]): void
  (e: 'reindex', doc: DocItem): void
  (e: 'upload'): void
  (e: 'download', doc: DocItem): void
}>()

const { onMove, onLeave } = usePointerGlow()

type SortKey = 'created_desc' | 'created_asc' | 'title_asc' | 'title_desc' | 'size_desc' | 'size_asc'

const STORAGE_KEY = (kb: number) => `doc_sort:${kb}`
const keyword = ref('')
const typeFilter = ref('all')
const sortKey = ref<SortKey>(
  (localStorage.getItem(STORAGE_KEY(props.kbId)) as SortKey) || 'created_desc',
)
const dateFrom = ref('')
const dateTo = ref('')
const page = ref(1)
const pageSize = ref(10)
const selected = ref<number[]>([])

watch(sortKey, (v) => localStorage.setItem(STORAGE_KEY(props.kbId), v))
watch([keyword, typeFilter, sortKey, dateFrom, dateTo, pageSize], () => {
  page.value = 1
})

const typeOptions = computed(() => {
  const set = new Set(props.docs.map((d) => d.docType).filter(Boolean))
  return [...set]
})

const filtered = computed(() => {
  const q = keyword.value.trim().toLowerCase()
  const arr = props.docs.filter((d) => {
    if (q && !d.title.toLowerCase().includes(q) && !d.docTypeDesc.toLowerCase().includes(q)) return false
    if (typeFilter.value !== 'all' && d.docType !== typeFilter.value) return false
    const day = (d.createdAt || '').slice(0, 10)
    if (dateFrom.value && day && day < dateFrom.value) return false
    if (dateTo.value && day && day > dateTo.value) return false
    return true
  })
  const [field, dir] = sortKey.value.split('_') as [string, 'asc' | 'desc']
  const mul = dir === 'asc' ? 1 : -1
  arr.sort((a, b) => {
    let cmp = 0
    if (field === 'created') {
      cmp = (new Date(a.createdAt).getTime() || 0) - (new Date(b.createdAt).getTime() || 0)
    } else if (field === 'title') {
      cmp = a.title.localeCompare(b.title, 'zh')
    } else if (field === 'size') {
      cmp = (a.fileSize || 0) - (b.fileSize || 0)
    }
    return cmp * mul
  })
  return arr
})

const paged = computed(() => paginateSlice(filtered.value, page.value, pageSize.value))

const allChecked = computed({
  get: () => paged.value.length > 0 && paged.value.every((d) => selected.value.includes(d.docId)),
  set: (v: boolean) => {
    const ids = paged.value.map((d) => d.docId)
    selected.value = v
      ? Array.from(new Set([...selected.value, ...ids]))
      : selected.value.filter((id) => !ids.includes(id))
  },
})

const TONES = ['violet', 'amber', 'sky', 'rose'] as const

function tone(row: DocItem, index: number) {
  if (row.docType === 'pdf') return 'amber'
  if (row.docType === 'txt') return 'rose'
  if (row.docType === 'md' || row.docType === 'markdown') return 'violet'
  return TONES[index % TONES.length]
}

function typeLabel(row: DocItem) {
  const t = (row.docType || '').toLowerCase()
  if (t === 'md' || t === 'markdown') return 'Markdown'
  if (t === 'pdf') return 'PDF'
  if (t === 'txt') return 'Text'
  return row.docTypeDesc || '文件'
}

function extBadge(row: DocItem) {
  return (row.docType || 'FILE').toUpperCase()
}

function blurb(row: DocItem) {
  const t = (row.docType || '').toLowerCase()
  if (t === 'md' || t === 'markdown') return 'Markdown 文档，可用于检索与问答'
  if (t === 'pdf') return 'PDF 文档，解析后参与知识库检索'
  if (t === 'txt') return '纯文本文档，适合规范与笔记入库'
  return '知识库文档'
}

function statusText(row: DocItem) {
  if (row.parseStatus === ParseStatus.Error) return PARSE_STATUS_LABEL[row.parseStatus]
  if (row.parseStatus !== ParseStatus.Parsed) return PARSE_STATUS_LABEL[row.parseStatus]
  if (row.indexStatus === IndexStatus.Failed) return INDEX_STATUS_LABEL[row.indexStatus]
  if (row.indexStatus === IndexStatus.Indexing) return INDEX_STATUS_LABEL[row.indexStatus]
  return PARSE_STATUS_LABEL[row.parseStatus]
}

function statusClass(row: DocItem) {
  if (row.parseStatus === ParseStatus.Error || row.indexStatus === IndexStatus.Failed) return 'is-err'
  if (
    row.parseStatus === ParseStatus.Processing ||
    row.parseStatus === ParseStatus.Pending ||
    row.indexStatus === IndexStatus.Indexing
  ) {
    return 'is-warn is-busy'
  }
  if (row.parseStatus === ParseStatus.Parsed) return 'is-ok'
  return 'is-muted'
}

function vectorText(row: DocItem) {
  const n = row.chunkCount >= 0 ? row.chunkCount : 0
  return n.toLocaleString('en-US')
}

function removeSelected() {
  const items = props.docs.filter((d) => selected.value.includes(d.docId))
  emit('delete-many', items)
  selected.value = []
}
</script>

<template>
  <div class="docs-card">
    <div class="docs-toolbar">
      <label class="docs-search">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8">
          <circle cx="11" cy="11" r="7" />
          <path d="M20 20l-3.2-3.2" stroke-linecap="round" />
        </svg>
        <input v-model="keyword" placeholder="输入文件名、关键词或内容进行搜索…" />
      </label>
      <select v-model="typeFilter" class="docs-select">
        <option value="all">全部类型</option>
        <option v-for="t in typeOptions" :key="t" :value="t">{{ t.toUpperCase() }}</option>
      </select>
      <select v-model="sortKey" class="docs-select">
        <option value="created_desc">最近修改</option>
        <option value="created_asc">最早上传</option>
        <option value="title_asc">文件名称</option>
        <option value="size_desc">文件大小</option>
      </select>
      <div class="docs-dates">
        <input v-model="dateFrom" type="date" title="开始日期" />
        <span>−</span>
        <input v-model="dateTo" type="date" title="结束日期" />
      </div>
      <button v-if="selected.length" class="docs-ghost" type="button" @click="removeSelected">
        删除所选 ({{ selected.length }})
      </button>
      <button class="docs-upload-btn" type="button" @click="emit('upload')">+ 上传文件</button>
    </div>

    <div v-if="loading && !docs.length" class="docs-skel">
      <div v-for="n in 4" :key="n" class="skeleton h-16 rounded-2xl" />
    </div>
    <p v-else-if="!filtered.length" class="docs-empty">暂无文档，点击右上角上传文件</p>
    <div v-else class="docs-table" :class="{ 'is-busy': loading }">
      <div class="docs-tr docs-tr--head">
        <label class="docs-check"><input v-model="allChecked" type="checkbox" /></label>
        <span>文件名称</span>
        <span>类型</span>
        <span>大小</span>
        <span>向量数量</span>
        <span>最后修改时间</span>
        <span>状态</span>
        <span>操作</span>
      </div>
      <div
        v-for="(row, i) in paged"
        :key="row.docId"
        class="docs-tr"
        :class="`tone-${tone(row, i)}`"
        @click="emit('view', row)"
        @pointermove="onMove"
        @pointerleave="onLeave"
      >
        <label class="docs-check" @click.stop>
          <input v-model="selected" type="checkbox" :value="row.docId" />
        </label>
        <div class="docs-file">
          <span class="docs-file__icon">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" opacity=".2" />
              <path d="M14 2v6h6" fill="none" stroke="currentColor" stroke-width="1.6" />
              <path d="M8 13h8M8 17h5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
            </svg>
          </span>
          <div class="docs-file__meta">
            <div class="docs-file__name">
              <b>{{ row.title }}</b>
              <em>{{ extBadge(row) }}</em>
            </div>
            <p>{{ blurb(row) }}</p>
          </div>
        </div>
        <span><em class="docs-type">{{ typeLabel(row) }}</em></span>
        <span class="docs-num">{{ formatFileSize(row.fileSize) }}</span>
        <span class="docs-num">{{ vectorText(row) }}</span>
        <span class="docs-time">{{ formatDate(row.createdAt, true) }}</span>
        <span>
          <i class="docs-status" :class="statusClass(row)">{{ statusText(row) }}</i>
        </span>
        <div class="docs-ops" @click.stop>
          <button type="button" title="查看" @click="emit('view', row)">
            <el-icon><View /></el-icon>
          </button>
          <button type="button" title="下载" @click="emit('download', row)">
            <el-icon><Download /></el-icon>
          </button>
          <el-dropdown trigger="click" @command="(c: string) => (c === 'reindex' ? emit('reindex', row) : emit('delete', row))">
            <button type="button" title="更多">
              <el-icon><MoreFilled /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="reindex" :icon="Refresh">重解析</el-dropdown-item>
                <el-dropdown-item command="delete" divided :icon="Delete">删除</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </div>
    </div>

    <div v-if="filtered.length" class="docs-pager">
      <span>共 {{ filtered.length }} 条</span>
      <select v-model.number="pageSize">
        <option :value="10">10 条/页</option>
        <option :value="20">20 条/页</option>
        <option :value="40">40 条/页</option>
      </select>
      <el-pagination
        :current-page="page"
        :page-size="pageSize"
        :total="filtered.length"
        layout="prev, pager, next"
        background
        small
        @current-change="(v: number) => (page = v)"
      />
    </div>
  </div>
</template>
