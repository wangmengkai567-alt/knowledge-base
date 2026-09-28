<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Delete, Search, Right } from '@element-plus/icons-vue'
import { formatDate } from '@/utils/format'
import EmptyState from '@/components/Common/EmptyState.vue'
import { useSearchHistoryStore } from '@/stores/history'

const router = useRouter()
const history = useSearchHistoryStore()
const keyword = ref('')
const typeFilter = ref<'all' | 'text' | 'doc'>('all')
const selected = ref<string[]>([])

function isQuestion(q: string) {
  return /[？?]/.test(q) || /(什么|如何|怎么|为什么|哪些)/.test(q)
}

const filtered = computed(() => {
  return history.items.filter((item) => {
    if (keyword.value.trim() && !item.query.toLowerCase().includes(keyword.value.trim().toLowerCase())) {
      return false
    }
    if (typeFilter.value === 'text' && !isQuestion(item.query)) return false
    if (typeFilter.value === 'doc' && isQuestion(item.query)) return false
    return true
  })
})

const allChecked = computed({
  get: () => filtered.value.length > 0 && filtered.value.every((i) => selected.value.includes(i.id)),
  set: (v: boolean) => {
    selected.value = v ? filtered.value.map((i) => i.id) : []
  },
})

function go(q: string) {
  router.push({ path: '/search', query: { q } })
}

function removeSelected() {
  selected.value.forEach((id) => history.remove(id))
  selected.value = []
}
</script>

<template>
  <div class="hist-page">
    <div class="hist-head">
      <div>
        <h2 class="page-title">检索历史</h2>
        <p class="page-subtitle">查看与管理你的历史搜索记录</p>
      </div>
      <div class="hist-head__art" aria-hidden="true" />
    </div>

    <div class="hist-card">
      <div class="hist-toolbar">
        <label class="hist-search">
          <el-icon><Search /></el-icon>
          <input v-model="keyword" placeholder="输入关键词搜索…" />
        </label>
        <el-select v-model="typeFilter" class="!w-32" size="default">
          <el-option label="全部类型" value="all" />
          <el-option label="文本搜索" value="text" />
          <el-option label="文档检索" value="doc" />
        </el-select>
        <el-button v-if="selected.length" type="danger" plain :icon="Delete" @click="removeSelected">
          删除所选
        </el-button>
        <el-button class="ml-auto" :icon="Delete" @click="history.clear()" :disabled="!history.items.length">
          清空记录
        </el-button>
      </div>

      <EmptyState
        v-if="!filtered.length"
        title="暂无检索历史"
        description="在首页或检索页发起搜索后，记录会显示在这里"
      />

      <div v-else class="hist-table">
        <div class="hist-tr hist-tr--head">
          <label class="hist-check"><input v-model="allChecked" type="checkbox" /></label>
          <span>搜索内容</span>
          <span>类型</span>
          <span>结果数</span>
          <span>搜索时间</span>
          <span>操作</span>
        </div>
        <div v-for="item in filtered" :key="item.id" class="hist-tr" @click="go(item.query)">
          <label class="hist-check" @click.stop>
            <input v-model="selected" type="checkbox" :value="item.id" />
          </label>
          <span class="hist-query">
            <el-icon class="text-brand-500"><Search /></el-icon>
            {{ item.query }}
          </span>
          <span>
            <em class="hist-tag" :class="isQuestion(item.query) ? 'is-text' : 'is-doc'">
              {{ isQuestion(item.query) ? '文本搜索' : '文档检索' }}
            </em>
          </span>
          <span class="tabular-nums text-ink-500">{{ item.resultCount }} 条结果</span>
          <span class="text-ink-400">{{ formatDate(item.timestamp, true) }}</span>
          <span class="hist-ops" @click.stop>
            <button class="icon-btn !h-8 !w-8" type="button" title="再次搜索" @click="go(item.query)">
              <el-icon><Right /></el-icon>
            </button>
            <button class="icon-btn !h-8 !w-8 !text-rose-500" type="button" title="删除" @click="history.remove(item.id)">
              <el-icon><Delete /></el-icon>
            </button>
          </span>
        </div>
      </div>
    </div>
  </div>
</template>
