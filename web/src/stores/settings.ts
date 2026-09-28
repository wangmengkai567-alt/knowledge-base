import { defineStore } from 'pinia'
import { reactive } from 'vue'

const STORAGE_KEY = 'akb_ui_settings'

export type UiSettings = {
  llmModel: string
  temperature: number
  maxTokens: number
  topP: number
  embeddingModel: string
  dimension: number
  defaultChunkSize: number
  defaultChunkOverlap: number
  autoIndex: boolean
}

const defaults: UiSettings = {
  llmModel: 'hy3',
  temperature: 0.3,
  maxTokens: 2048,
  topP: 0.9,
  embeddingModel: 'kinfra-text-embedding-0.6b',
  dimension: 1024,
  defaultChunkSize: 500,
  defaultChunkOverlap: 50,
  autoIndex: true,
}

function loadStored(): Partial<UiSettings> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

export const useSettingsStore = defineStore('settings', () => {
  const config = reactive<UiSettings>({ ...defaults, ...loadStored() })

  function save() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...config }))
  }

  return { config, save }
})
