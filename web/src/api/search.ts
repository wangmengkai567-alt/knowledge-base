import { post } from './request'
import { fileExt } from '@/utils/format'
import type { SearchHit, RagSource } from '@/types'

/** 去掉扩展名作为可读标题 */
function stripExt(name: string): string {
  const i = name.lastIndexOf('.')
  return i > 0 ? name.slice(0, i) : name
}

function mapSource(s: any, kbId: number): RagSource {
  return {
    chunkId: s.chunk_id,
    content: s.content,
    score: s.score,
    documentId: s.document_id,
    documentFilename: s.document_filename,
    kbId: s.knowledge_base_id ?? kbId,
  }
}

export type RagStreamHandler = (event: {
  type: 'sources' | 'chunk' | 'error'
  sources?: RagSource[]
  delta?: string
  model?: string
  error?: string
}) => void

async function readSseStream(res: Response, onEvent: (data: any) => void): Promise<void> {
  if (!res.body) throw new Error('No response body')
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const parts = buf.split('\n\n')
    buf = parts.pop() || ''
    for (const part of parts) {
      for (const raw of part.split('\n')) {
        const line = raw.trim()
        if (!line.startsWith('data:')) continue
        const data = line.slice(5).trim()
        if (!data || data === '[DONE]') return
        onEvent(JSON.parse(data))
      }
    }
  }
}

/** 单知识库向量检索：POST /api/v1/knowledge/bases/{kbId}/retrieve */
export const searchApi = {
  retrieve: (
    kbId: number,
    query: string,
    topK = 10,
  ): Promise<{ results: SearchHit[]; total: number }> =>
    post<{ query: string; knowledge_base_id: number; results: any[]; total: number }>(
      `/v1/knowledge/bases/${kbId}/retrieve`,
      { query, top_k: topK },
    ).then((res) => ({
      results: res.results.map(
        (r): SearchHit => ({
          chunkId: r.chunk_id,
          docId: r.document_id,
          kbId: res.knowledge_base_id,
          docName: r.document_filename,
          docTitle: stripExt(r.document_filename),
          content: r.content,
          position: r.position,
          fileType: fileExt(r.document_filename),
          score: r.score,
          keywords: [],
          updatedAt: r.document_updated_at || undefined,
        }),
      ),
      total: res.total,
    })),

  /**
   * SSE 流式 RAG：POST /api/v1/knowledge/bases/{kbId}/rag/query/stream
   * EventSource 只支持 GET，因此用 fetch + ReadableStream 解析 data: 行。
   */
  ragSummaryStream: async (
    kbId: number,
    query: string,
    topK: number,
    onEvent: RagStreamHandler,
    signal?: AbortSignal,
    chunkIds?: number[],
    knowledgeBaseIds?: number[],
  ): Promise<void> => {
    const base = import.meta.env.VITE_API_BASE_URL || '/api'
    const token = localStorage.getItem('access_token')
    const res = await fetch(`${base}/v1/knowledge/bases/${kbId}/rag/query/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({
        query,
        top_k: topK,
        ...(chunkIds !== undefined ? { chunk_ids: chunkIds } : {}),
        ...(knowledgeBaseIds?.length ? { knowledge_base_ids: knowledgeBaseIds } : {}),
      }),
      signal,
    })
    if (res.status === 401) {
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      if (location.pathname !== '/login') location.href = '/login'
      throw new Error('Unauthorized')
    }
    if (!res.ok) {
      let msg = `RAG stream failed (${res.status})`
      try {
        const err = await res.json()
        msg = err.message || msg
      } catch {
        /* ignore */
      }
      throw new Error(msg)
    }
    await readSseStream(res, (data) => {
      if (data.type === 'sources') {
        onEvent({
          type: 'sources',
          sources: (data.sources || []).map((s: any) => mapSource(s, kbId)),
        })
      } else if (data.type === 'chunk') {
        onEvent({ type: 'chunk', delta: data.delta || '', model: data.model })
      } else if (data.type === 'error') {
        onEvent({ type: 'error', error: data.error || 'stream error' })
      }
    })
  },
}
