/** 文档解析状态（与后端 DocumentStatus 对齐：PENDING=1, PARSED=2, ERROR=3, PROCESSING=4） */
export enum ParseStatus {
  Pending = 1, // 待解析
  Parsed = 2, // 解析成功
  Error = 3, // 解析失败
  Processing = 4, // 解析中
}

/** 文档索引状态 */
export enum IndexStatus {
  None = 0, // 未索引
  Indexing = 1, // 索引中
  Indexed = 2, // 已索引
  Failed = 3, // 索引失败
}

/** 向量嵌入状态 */
export enum EmbeddingStatus {
  Pending = 0, // 待嵌入
  Done = 1, // 已完成
  Failed = 2, // 失败
}

export const PARSE_STATUS_LABEL: Record<number, string> = {
  1: '待解析',
  2: '解析成功',
  3: '解析失败',
  4: '解析中',
}

export const INDEX_STATUS_LABEL: Record<number, string> = {
  0: '未索引',
  1: '索引中',
  2: '已索引',
  3: '索引失败',
}

export const EMBEDDING_STATUS_LABEL: Record<number, string> = {
  0: '待嵌入',
  1: '已入库',
  2: '失败',
}

/** 文档列表项（GET /api/v1/kbs/{kbId}/docs） */
export interface DocItem {
  docId: number
  kbId: number
  title: string
  docType: string
  docTypeDesc: string
  tags: string[]
  charCount: number
  /** 原始文件字节数 */
  fileSize: number
  chunkCount: number
  parseStatus: ParseStatus
  parseStatusDesc: string
  indexStatus: IndexStatus
  indexStatusDesc: string
  version: number
  language: string
  createdBy?: { userId: number; nickname: string }
  createdAt: string
  indexedAt?: string
}

/** 文档详情（GET /api/v1/kbs/{kbId}/docs/{docId}） */
export interface DocDetail extends DocItem {
  /** 完整的解析后文本（未切块），用于 Markdown 整体预览与目录生成 */
  fullContent?: string
  sourceUrl?: string
  parseErrorMsg?: string | null
  indexErrorMsg?: string | null
  parsedAt?: string
  file?: {
    fileId: number
    originalName: string
    mimeType: string
    fileSize: number
    fileSizeDesc: string
  }
  updatedAt: string
}

/** 文档分块（GET .../chunks） */
export interface Chunk {
  chunkId: number
  chunkIndex: number
  content: string
  charCount: number
  embeddingStatus: EmbeddingStatus
  embeddingStatusDesc: string
  createdAt: string
}
