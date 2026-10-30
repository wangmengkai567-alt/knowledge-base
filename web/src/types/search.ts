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
  /** 排序分；类型和计算方式见 scoreType / scoreMethod */
  score: number | null
  scoreType?: string | null
  scoreMethod?: string | null
  scores?: { kind: string; value: number; method: string }[]
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

interface SourceBase {
  citationId: number
  content: string
  truncated: boolean
}

export interface KnowledgeSource extends SourceBase {
  sourceType: 'knowledge_base'
  chunkIds: number[]
  segments: { chunk_id: number; position: number; start: number; end: number; content: string }[]
  documentId: number
  documentFilename: string
  documentUpdatedAt: string | null
  kbId: number
}

export interface WebSource extends SourceBase {
  sourceType: 'web_search_summary' | 'web_page'
  url: string
  title: string
  fetchedAt: string
}

export type RagSource = KnowledgeSource | WebSource
export type RunStatus = 'running' | 'completed' | 'insufficient' | 'budget_exhausted' |
  'usage_unknown' | 'tool_failed' | 'model_failed' | 'invalid_answer' | 'timeout' | 'cancelled'

export interface RunUsage {
  model_calls: number
  tool_calls: number
  prompt_tokens: number
  completion_tokens: number
  estimated_input_tokens: number
  settled_tokens: number
  actual_total_tokens: number | null
  reserved_tokens: number
  final_reserved_tokens: number
  calls: { run_id: string; call: number; phase: 'exploration' | 'final';
    estimated_input_tokens: number; output_limit: number; reservation: number;
    state: 'reserved' | 'settled' | 'unknown'; prompt_tokens: number | null;
    completion_tokens: number | null; provider_total_tokens: number | null; unknown_reason: string | null }[]
  unreported_model_calls: number
}

export interface RunEvent {
  sequence: number
  kind: string
  elapsed_ms: number
  data: Record<string, unknown>
}

export interface RunResult {
  runId: string
  status: RunStatus
  stopReason: string
  explorationStopReason: string
  errorCode: string | null
  answer: string
  model: string
  sources: RagSource[]
  results: SearchHit[]
  usage: RunUsage
}

/** 收藏项 */
export interface FavoriteItem {
  chunkId: number
  docId: number
  kbId: number
  docName: string
  docTitle: string
  content: string
  score: number | null
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
