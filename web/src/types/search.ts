/** 检索命中（前端聚合后的统一结构） */
export interface SearchHit {
  chunkId: number
  docId: number
  kbId: number
  /** 文档名称（带扩展名） */
  docName: string
  /** 文档标题（去扩展名后的可读标题） */
  docTitle: string
  /** 命中内容片段 */
  content: string
  /** 分块在文档中的位置序号 */
  position: number
  /** 父级文档类型（用于筛选） */
  fileType: string // pdf / md / txt
  /** 相似度分数 0~1 */
  score: number
  /** 关键词 */
  keywords: string[]
  /** 命中文档更新时间（ISO），供时间筛选 */
  updatedAt?: string
}

/** 检索筛选条件 */
export interface SearchFilters {
  /** 选中的知识库 ID，空表示全部 */
  kbIds: number[]
  /** 文件类型筛选 */
  fileTypes: string[]
  /** 时间范围：day / week / month / year / '' */
  timeRange: '' | 'day' | 'week' | 'month' | 'year'
}

export type SortOption = 'relevance' | 'name'

/** RAG 引用来源 */
export interface RagSource {
  chunkId: number
  content: string
  score: number
  documentId: number
  documentFilename: string
  kbId: number
}

/** RAG 摘要响应 */
export interface RagSummary {
  answer: string
  model: string
  sources: RagSource[]
  sourceCount: number
}

/** 收藏项 */
export interface FavoriteItem {
  chunkId: number
  docId: number
  kbId: number
  docName: string
  docTitle: string
  content: string
  score: number
  keyword: string
  savedAt: string
}

/** 搜索历史项 */
export interface SearchHistoryItem {
  id: string
  query: string
  resultCount: number
  timestamp: string
}
