import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import type { FavoriteItem } from '@/types'

const KEY = 'ai_kb_favorites'

function load(): FavoriteItem[] {
  try {
    return JSON.parse(localStorage.getItem(KEY) || '[]')
  } catch {
    return []
  }
}

export const useFavoritesStore = defineStore('favorites', () => {
  const items = ref<FavoriteItem[]>(load())

  watch(
    items,
    (v) => localStorage.setItem(KEY, JSON.stringify(v)),
    { deep: true },
  )

  function has(chunkId: number): boolean {
    return items.value.some((i) => i.chunkId === chunkId)
  }

  function add(item: Omit<FavoriteItem, 'savedAt'>) {
    if (has(item.chunkId)) return
    items.value.unshift({ ...item, savedAt: new Date().toISOString() })
  }

  function remove(chunkId: number) {
    items.value = items.value.filter((i) => i.chunkId !== chunkId)
  }

  function toggle(item: Omit<FavoriteItem, 'savedAt'>) {
    if (has(item.chunkId)) remove(item.chunkId)
    else add(item)
  }

  return { items, has, add, remove, toggle }
})
