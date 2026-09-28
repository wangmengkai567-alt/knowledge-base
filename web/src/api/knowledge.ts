import { get, post, patch, del } from './request'
import { KbStatus, KB_STATUS_LABEL } from '@/types'
import type { KnowledgeBase, KnowledgeBasePayload } from '@/types'

/**
 * 知识库目录走后端 CRUD。
 * 旧版把名称存在 localStorage（akb_kb_list），首次拉取时把同 id 的名称/描述写回后端后清除。
 */

const LEGACY_STORAGE_KEY = 'akb_kb_list'
const MIGRATED_KEY = 'akb_kb_migrated'

interface BackendKb {
  id: number
  name: string
  description: string
  icon: string
  status: number
  owner_id: number
  embedding_model: string
  chunk_strategy: string
  chunk_size: number
  chunk_overlap: number
  doc_count: number
  chunk_count: number
  created_at: string | null
  updated_at: string | null
}

function mapKb(k: BackendKb): KnowledgeBase {
  const status = (k.status ?? KbStatus.Normal) as KbStatus
  return {
    kbId: k.id,
    name: k.name,
    description: k.description || '',
    icon: k.icon || 'folder',
    docCount: k.doc_count ?? 0,
    chunkCount: k.chunk_count ?? 0,
    embeddingModel: k.embedding_model,
    chunkStrategy: k.chunk_strategy,
    chunkSize: k.chunk_size,
    chunkOverlap: k.chunk_overlap,
    status,
    statusDesc: KB_STATUS_LABEL[status] ?? '正常',
    createdBy: k.owner_id ? { userId: k.owner_id, nickname: '' } : undefined,
    createdAt: k.created_at || '',
    updatedAt: k.updated_at || '',
  }
}

function toCreateBody(payload: KnowledgeBasePayload) {
  return {
    name: payload.name,
    description: payload.description || '',
    icon: payload.icon || 'folder',
  }
}

async function migrateLegacyNames(list: KnowledgeBase[]): Promise<KnowledgeBase[]> {
  if (localStorage.getItem(MIGRATED_KEY)) return list
  let locals: KnowledgeBase[] = []
  try {
    const raw = localStorage.getItem(LEGACY_STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed)) locals = parsed
    }
  } catch {
    /* 损坏数据忽略 */
  }
  localStorage.setItem(MIGRATED_KEY, '1')
  if (!locals.length) {
    localStorage.removeItem(LEGACY_STORAGE_KEY)
    return list
  }
  for (const local of locals) {
    if (!local.kbId || !local.name) continue
    const found = list.find((k) => k.kbId === local.kbId)
    if (!found) continue
    try {
      const updated = await patch<BackendKb>(`/v1/knowledge/bases/${local.kbId}`, {
        name: local.name,
        description: local.description || '',
        icon: local.icon || 'folder',
      })
      Object.assign(found, mapKb(updated))
    } catch {
      /* 单个失败不阻断 */
    }
  }
  localStorage.removeItem(LEGACY_STORAGE_KEY)
  return list
}

export const knowledgeApi = {
  /** GET /api/v1/knowledge/bases → { knowledge_bases, total } */
  list: async (): Promise<KnowledgeBase[]> => {
    const res = await get<{ knowledge_bases: BackendKb[]; total: number }>(
      '/v1/knowledge/bases',
    )
    const mapped = (res.knowledge_bases || []).map(mapKb)
    return migrateLegacyNames(mapped)
  },

  /** GET /api/v1/knowledge/bases/{kbId} */
  detail: (kbId: number): Promise<KnowledgeBase> =>
    get<BackendKb>(`/v1/knowledge/bases/${kbId}`).then(mapKb),

  /** POST /api/v1/knowledge/bases */
  create: (payload: KnowledgeBasePayload): Promise<KnowledgeBase> =>
    post<BackendKb>('/v1/knowledge/bases', toCreateBody(payload)).then(mapKb),

  /** PATCH /api/v1/knowledge/bases/{kbId} */
  update: (kbId: number, payload: KnowledgeBasePayload): Promise<KnowledgeBase> =>
    patch<BackendKb>(`/v1/knowledge/bases/${kbId}`, toCreateBody(payload)).then(mapKb),

  /** DELETE /api/v1/knowledge/bases/{kbId} → 204 */
  remove: (kbId: number): Promise<void> => del<void>(`/v1/knowledge/bases/${kbId}`),
}
