import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import type { SearchHistoryItem } from '@/types'

const KEY = 'ai_kb_search_history'
const MAX = 30

function load(): SearchHistoryItem[] {
  try {
    return JSON.parse(localStorage.getItem(KEY) || '[]')
  } catch {
    return []
  }
}

export const useSearchHistoryStore = defineStore('history', () => {
  const items = ref<SearchHistoryItem[]>(load())

  watch(
    items,
    (v) => localStorage.setItem(KEY, JSON.stringify(v)),
    { deep: true },
  )

  function add(query: string, resultCount: number) {
    const q = query.trim()
    if (!q) return
    items.value = items.value.filter((i) => i.query !== q)
    items.value.unshift({
      id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
      query: q,
      resultCount,
      timestamp: new Date().toISOString(),
    })
    if (items.value.length > MAX) items.value.length = MAX
  }

  function remove(id: string) {
    items.value = items.value.filter((i) => i.id !== id)
  }

  function clear() {
    items.value = []
  }

  return { items, add, remove, clear }
})
