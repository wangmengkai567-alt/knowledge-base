import { defineStore } from 'pinia'
import { ref } from 'vue'
import { knowledgeApi } from '@/api/knowledge'
import type { KnowledgeBase, KnowledgeBasePayload } from '@/types'

export const useKnowledgeStore = defineStore('knowledge', () => {
  const kbs = ref<KnowledgeBase[]>([])
  const loading = ref(false)

  /**
   * 加载知识库列表。名称/计数均来自后端 GET /knowledge/bases，
   * 不再从 localStorage 读注册表，也不再按库扇出 documents 补计数。
   */
  let loadPromise: Promise<void> | null = null

  async function load(_params: { keyword?: string } = {}) {
    if (loadPromise) return loadPromise
    if (kbs.value.length > 0) return
    loading.value = true
    loadPromise = (async () => {
      try {
        kbs.value = await knowledgeApi.list()
      } finally {
        loading.value = false
        loadPromise = null
      }
    })()
    return loadPromise
  }

  async function create(payload: KnowledgeBasePayload): Promise<KnowledgeBase> {
    const kb = await knowledgeApi.create(payload)
    kbs.value.push(kb)
    return kb
  }

  async function update(kbId: number, payload: KnowledgeBasePayload): Promise<KnowledgeBase> {
    const kb = await knowledgeApi.update(kbId, payload)
    const idx = kbs.value.findIndex((k) => k.kbId === kbId)
    if (idx !== -1) kbs.value[idx] = kb
    return kb
  }

  async function remove(kbId: number): Promise<void> {
    await knowledgeApi.remove(kbId)
    kbs.value = kbs.value.filter((k) => k.kbId !== kbId)
  }

  function getKbName(id: number): string {
    return kbs.value.find((k) => k.kbId === id)?.name || `知识库#${id}`
  }

  return { kbs, loading, load, create, update, remove, getKbName }
})
