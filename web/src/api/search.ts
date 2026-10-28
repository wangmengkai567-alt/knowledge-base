import { post } from './request'
import { fileExt } from '@/utils/format'
import type { SearchHit, RagSource, RunEvent, RunResult } from '@/types'

/** 去掉扩展名作为可读标题 */
function stripExt(name: string): string {
  const i = name.lastIndexOf('.')
  return i > 0 ? name.slice(0, i) : name
}

function mapSource(s: any): RagSource {
  const base = { citationId: s.citation_id, sourceType: s.source_type,
    content: s.content, truncated: s.truncated }
  if (s.source_type === 'knowledge_base') return {
    ...base, sourceType: 'knowledge_base', chunkIds: s.chunk_ids, segments: s.segments,
    documentId: s.document_id, documentFilename: s.document_filename,
    documentUpdatedAt: s.document_updated_at, kbId: s.knowledge_base_id,
  }
  if (s.source_type === 'web_search_summary' || s.source_type === 'web_page') return {
    ...base, sourceType: s.source_type, url: s.url, title: s.title, fetchedAt: s.fetched_at,
  }
  throw new Error('未知的资料来源类型')
}

function mapHit(r: any): SearchHit {
  return {
    chunkId: r.chunk_id, docId: r.document_id, kbId: r.knowledge_base_id,
    docName: r.document_filename, docTitle: stripExt(r.document_filename),
    content: r.content, position: r.position, fileType: fileExt(r.document_filename),
    score: r.score, scoreType: r.score_type, scoreMethod: r.score_method, scores: r.scores,
    keywords: [], updatedAt: r.document_updated_at || undefined,
  }
}

export type RagStreamHandler = (event:
  | { type: 'event'; runId: string; event: RunEvent; sources?: RagSource[]; results?: SearchHit[] }
  | { type: 'result'; result: RunResult }
) => void

async function readSseStream(res: Response, onEvent: (data: any) => void): Promise<void> {
  if (!res.body) throw new Error('问答连接没有响应正文')
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let finished = false
  try {
    while (!finished) {
      const { done, value } = await reader.read()
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })
      let end: number
      while ((end = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, end)
        buffer = buffer.slice(end + 2)
        const data = frame.split('\n').filter(line => line.startsWith('data:'))
          .map(line => line.slice(5).trimStart()).join('\n')
        if (data === '[DONE]') { finished = true; break }
        if (data) onEvent(JSON.parse(data))
      }
      if (done && !finished) throw new Error('问答连接中断，请重新提问')
    }
  } finally {
    await reader.cancel().catch(() => {})
    reader.releaseLock()
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
      results: res.results.map(mapHit),
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
    knowledgeBaseIds: number[] = [kbId],
    allowWeb = false,
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
        knowledge_base_ids: knowledgeBaseIds,
        allow_web: allowWeb,
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
    let runId = ''
    let sequence = 0
    let terminal = false
    await readSseStream(res, (data) => {
      if (data.version !== 4) throw new Error('问答协议已更新，请刷新页面')
      if (data.type === 'error') throw new Error(data.error)
      if (terminal) throw new Error('收到终态之后的问答事件')
      if (!data.run_id || (runId && runId !== data.run_id)) throw new Error('问答运行编号不一致')
      runId = data.run_id
      if (data.type === 'event') {
        if (data.sequence !== sequence + 1) throw new Error('问答过程事件次序不完整')
        sequence = data.sequence
        onEvent({ type: 'event', runId, event: {
          sequence, kind: data.kind, elapsed_ms: data.elapsed_ms, data: data.data,
        }, ...(data.kind === 'sources_updated' ? {
          sources: data.data.sources.map(mapSource), results: data.data.retrieval_results.map(mapHit),
        } : {}) })
      } else if (data.type === 'result') {
        terminal = true
        onEvent({ type: 'result', result: {
          runId, status: data.status, stopReason: data.stop_reason, errorCode: data.error_code,
          answer: data.answer, model: data.model, usage: data.usage,
          sources: data.sources.map(mapSource), results: data.retrieval_results.map(mapHit),
        } })
      } else throw new Error('未知的问答事件')
    })
    if (!terminal) throw new Error('问答没有返回结束状态，请重新提问')
  },
}
