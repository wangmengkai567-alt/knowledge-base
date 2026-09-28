import { get, post } from './request'
import type { LoginRequest, LoginResponse, User } from '@/types'

/** 昵称规范化：后端 nickname 可能为空/缺失/异常（如 "???"、"？？？"、纯标点）。
 * 这类占位乱码一律视为无效，回退到邮箱 @ 前的可读部分，避免右上角显示乱码或空昵称。 */
function normalizeNickname(nickname: any, email?: string): string {
  const n = nickname != null ? String(nickname).trim() : ''
  // 视为无效昵称：空，或由问号/标点/空白构成的占位串（如 "???"、"？？？"）
  const invalid = !n || /^[\s?!?？.。,，_~`-]+$/.test(n)
  if (!invalid) return n
  if (email && typeof email === 'string') {
    const local = email.split('@')[0]
    if (local) return local
  }
  return '用户'
}

/** 将后端 snake_case 用户映射为前端 User */
function mapUser(u: any): User {
  return {
    userId: u.user_id,
    email: u.email,
    nickname: normalizeNickname(u.nickname, u.email),
    status: u.status,
  }
}

export const authApi = {
  /** POST /api/v1/users/login → { access_token, refresh_token, expires_in, user } */
  login: (data: LoginRequest): Promise<LoginResponse> =>
    post<any>('/v1/users/login', data).then((res) => ({
      accessToken: res.access_token,
      refreshToken: res.refresh_token,
      expiresIn: res.expires_in,
      user: mapUser(res.user),
    })),
  /** GET /api/v1/users/me → { user_id, email, nickname, status } */
  me: (): Promise<User> => get<any>('/v1/users/me').then(mapUser),
}
