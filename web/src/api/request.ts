import axios, { type AxiosInstance, type AxiosRequestConfig, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
import { mockAdapter } from './mock'

export const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

const http: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 30000,
})

// Mock 模式：使用本地数据引擎，无需后端即可演示
if (USE_MOCK) {
  http.defaults.adapter = mockAdapter as unknown as AxiosInstance['defaults']['adapter']
}

// 请求拦截：注入 Token
http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = localStorage.getItem('access_token')
  if (token) {
    config.headers.set('Authorization', `Bearer ${token}`)
  }
  return config
})

// 响应拦截：真实后端返回裸数据（无 data 信封）；错误格式为 { code, reason, message }
http.interceptors.response.use(
  (response: AxiosResponse) => {
    const body = response.data
    // 若响应体带 code 字段，说明是错误响应（FastAPI 统一异常格式）
    if (body && typeof body === 'object' && !Array.isArray(body) && 'code' in body) {
      ElMessage.error(body.message || '请求失败')
      return Promise.reject(new Error(body.message || 'Business error'))
    }
    // 成功：直接返回后端裸数据
    return body
  },
  (error) => {
    const status = error?.response?.status
    // 限流（429）：交由 withRetry 退避后重试一次；此处给出节流提示，避免刷屏
    if (status === 429) {
      notifyRateLimit()
      return Promise.reject(error)
    }
    if (status === 401) {
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      if (location.pathname !== '/login') {
        location.href = '/login'
      }
    }
    const data = error?.response?.data
    const msg = data?.message || error?.message || '网络异常，请稍后重试'
    ElMessage.error(msg)
    return Promise.reject(error)
  },
)

export default http

/* ===================== 统一请求辅助 ===================== */

// 429 提示节流：多个并行请求同时被限流时，只提示一次，避免刷屏
let _rateMsgAt = 0
function notifyRateLimit() {
  const now = Date.now()
  if (now - _rateMsgAt > 3000) {
    _rateMsgAt = now
    ElMessage.warning('请求过于频繁，正在自动重试…')
  }
}

/**
 * 统一请求辅助。
 * 对 429（限流）做「退避后重试一次」：读取后端返回的 X-RateLimit-Reset，
 * 等待窗口重置后重试，使被限流的扇出请求在窗口恢复后能拿到结果，而不是
 * 持续静默失败。仅重试一次，避免重试风暴（每次重试用同一 fn，不会叠加新请求）。
 * 其余错误直接抛出，由调用方 .catch 处理。
 */
async function withRetry<T>(fn: () => Promise<T>, attempt = 0): Promise<T> {
  try {
    return await fn()
  } catch (err: any) {
    const status = err?.response?.status
    if (status === 429 && attempt < 1) {
      const reset = Number(err?.response?.headers?.['x-ratelimit-reset']) || 2
      const wait = Math.min(Math.max(reset, 1), 30)
      // 抖动：打散并行请求的退避时刻，避免多个扇出请求在同一瞬间重试形成二次洪峰
      const jitter = Math.random() * 800
      await new Promise((r) => setTimeout(r, wait * 1000 + jitter))
      return withRetry(fn, attempt + 1)
    }
    throw err
  }
}

type R<T> = Promise<T>

/** GET：直接返回后端裸数据 */
export function get<T>(url: string, params?: Record<string, any>): R<T> {
  return withRetry(() => http.get(url, { params }).then((r: any) => r as T))
}

/** POST：直接返回后端裸数据；可传 timeout / onUploadProgress 等 axios 配置 */
export function post<T>(url: string, data?: any, config?: AxiosRequestConfig): R<T> {
  return withRetry(() => http.post(url, data, config).then((r: any) => r as T))
}

/** PATCH：直接返回后端裸数据 */
export function patch<T>(url: string, data?: any): R<T> {
  return withRetry(() => http.patch(url, data).then((r: any) => r as T))
}

/** DELETE：直接返回后端裸数据 */
export function del<T>(url: string): R<T> {
  return withRetry(() => http.delete(url).then((r: any) => r as T))
}
