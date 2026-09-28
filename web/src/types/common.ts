/** 统一 API 响应结构（后端约定：{ code, message, data, pagination }） */
export interface ApiResponse<T = unknown> {
  code: string
  message: string
  data: T
  pagination?: Pagination
}

/** 分页元信息 */
export interface Pagination {
  page: number
  pageSize: number
  total: number
  totalPages: number
}

/** 分页请求参数 */
export interface PageParams {
  page?: number
  pageSize?: number
}

/** 列表请求通用返回 */
export interface PaginatedList<T> {
  list: T[]
  pagination: Pagination
}
