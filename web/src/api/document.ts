import { get, post, del } from './request'
import { fileExt, formatFileSize } from '@/utils/format'
import { PARSE_STATUS_LABEL, EMBEDDING_STATUS_LABEL, INDEX_STATUS_LABEL, IndexStatus } from '@/types'
import type { DocItem, DocDetail, Chunk } from '@/types'

/** 去掉扩展名作为可读标题 */
function stripExt(name: string): string {
  const i = name.lastIndexOf('.')
  return i > 0 ? name.slice(0, i) : name
}

/** 后端 DocumentResponse → 前端 DocDetail（含 DocItem 全部字段 + 详情扩展） */
function mapDoc(d: any): DocDetail {
  const ext = fileExt(d.filename || '')
  const status = d.status as number
  const charCount = d.parsed_content ? String(d.parsed_content).length : 0
  const pending = Number(d.embedding_pending_count ?? 0)
  const failed = Number(d.embedding_failed_count ?? 0)
  const completed = Number(d.embedding_completed_count ?? 0)
  let indexStatus: IndexStatus
  if (failed > 0) indexStatus = IndexStatus.Failed
  else if (pending > 0 || status === 4) indexStatus = IndexStatus.Indexing
  else if (completed > 0) indexStatus = IndexStatus.Indexed
  else indexStatus = IndexStatus.None
  return {
    docId: d.id,
    kbId: d.knowledge_base_id,
    title: stripExt(d.filename || '未命名文档'),
    docType: ext,
    docTypeDesc: ext ? ext.toUpperCase() : 'FILE',
    tags: [],
    charCount,
    fileSize: d.file_size || 0,
    // 后端 list_documents 已聚合返回 chunk_count；未提供时置 -1，
    // 由知识库 store 降级为逐文档统计（仅后端未重启该聚合时使用）。
    chunkCount: typeof d.chunk_count === 'number' ? d.chunk_count : -1,
    parseStatus: status,
    parseStatusDesc: PARSE_STATUS_LABEL[status] ?? '未知',
    indexStatus,
    indexStatusDesc: INDEX_STATUS_LABEL[indexStatus] ?? '未知',
    version: 1,
    language: '',
    createdAt: d.created_at || '',
    indexedAt: status === 2 ? d.created_at || '' : undefined,
    // DocDetail 扩展字段
    fullContent: d.parsed_content ? String(d.parsed_content) : undefined,
    parseErrorMsg: d.error_message || null,
    parsedAt: d.created_at || '',
    file: {
      fileId: d.id,
      originalName: d.filename || '未命名文档',
      mimeType: d.mime_type || '',
      fileSize: d.file_size || 0,
      fileSizeDesc: formatFileSize(d.file_size || 0),
    },
    updatedAt: d.created_at || '',
  }
}

/** 后端 ChunkResponse → 前端 Chunk */
function mapChunk(c: any): Chunk {
  return {
    chunkId: c.id,
    chunkIndex: c.position,
    content: c.content,
    charCount: c.content ? String(c.content).length : 0,
    embeddingStatus: c.embedding_status,
    embeddingStatusDesc: EMBEDDING_STATUS_LABEL[c.embedding_status] ?? '未知',
    createdAt: c.created_at || '',
  }
}

export interface DocListParams {
  keyword?: string
  parseStatus?: number
  indexStatus?: number
  docType?: string
  orderBy?: string
  order?: string
}

export const documentApi = {
  /** GET /api/v1/knowledge/bases/{kbId}/documents → { documents, total } */
  list: (kbId: number, params: DocListParams = {}): Promise<DocItem[]> =>
    get<{ documents: any[]; total: number }>(`/v1/knowledge/bases/${kbId}/documents`, params).then(
      (r) => r.documents.map(mapDoc),
    ),

  /** GET /api/v1/knowledge/bases/{kbId}/documents/{docId} → DocumentResponse */
  detail: (kbId: number, docId: number): Promise<DocDetail> =>
    get<any>(`/v1/knowledge/bases/${kbId}/documents/${docId}`).then(mapDoc as (d: any) => DocDetail),

  /** DELETE /api/v1/knowledge/bases/{kbId}/documents/{docId} → 204 No Content */
  remove: (kbId: number, docId: number): Promise<void> =>
    del<void>(`/v1/knowledge/bases/${kbId}/documents/${docId}`),

  /** GET /api/v1/knowledge/bases/{kbId}/documents/{docId}/chunks → { chunks, total } */
  chunks: (
    kbId: number,
    docId: number,
    params: { page?: number; pageSize?: number } = {},
  ): Promise<{ list: Chunk[]; total: number }> =>
    get<{ chunks: any[]; total: number }>(
      `/v1/knowledge/bases/${kbId}/documents/${docId}/chunks`,
      params,
    ).then((r) => ({ list: r.chunks.map(mapChunk), total: r.total ?? r.chunks.length })),

  /**
   * POST /api/v1/knowledge/bases/{kbId}/documents（multipart/form-data）
   * - file: 主文档（.md/.pdf/.txt）
   */
  upload: (
    kbId: number,
    file: File,
    options: {
      onProgress?: (percent: number) => void
    } = {},
  ): Promise<DocItem> => {
    const form = new FormData()
    form.append('file', file)
    return post<any>(`/v1/knowledge/bases/${kbId}/documents`, form, {
      timeout: 600000,
      onUploadProgress: (e) => {
        if (!options.onProgress || !e.total) return
        options.onProgress(Math.min(100, Math.round((e.loaded / e.total) * 100)))
      },
    }).then(mapDoc)
  },

  /** POST /api/v1/knowledge/bases/{kbId}/documents/{docId}/parse */
  reparse: (kbId: number, docId: number, force = false): Promise<DocDetail> =>
    post<any>(
      `/v1/knowledge/bases/${kbId}/documents/${docId}/parse`,
      { force },
      { timeout: 600000 },
    ).then(mapDoc),

  /** POST /api/v1/knowledge/bases/{kbId}/documents/{docId}/embed — 重试 FAILED/PENDING 嵌入 */
  retryEmbed: (kbId: number, docId: number): Promise<DocDetail> =>
    post<any>(`/v1/knowledge/bases/${kbId}/documents/${docId}/embed`, undefined, {
      timeout: 180000,
    }).then(mapDoc),
}
