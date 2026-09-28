/** 知识库状态 */
export enum KbStatus {
  Normal = 1, // 正常
  Archived = 2, // 归档（不可提问）
  Reindexing = 3, // 索引重建中
}

export const KB_STATUS_LABEL: Record<number, string> = {
  1: '正常',
  2: '归档',
  3: '索引重建中',
}

/** 知识库列表项 */
export interface KnowledgeBase {
  kbId: number
  name: string
  description: string
  icon?: string
  docCount: number
  chunkCount: number
  totalTokensUsed?: number
  embeddingModel?: string
  chunkStrategy?: string
  chunkSize?: number
  chunkOverlap?: number
  status: KbStatus
  statusDesc: string
  createdBy?: { userId: number; nickname: string }
  createdAt: string
  updatedAt: string
}

/** 创建/更新知识库入参 */
export interface KnowledgeBasePayload {
  name: string
  description?: string
  embeddingModel?: string
  chunkStrategy?: string
  chunkSize?: number
  chunkOverlap?: number
  icon?: string
}
