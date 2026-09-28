/** 当前登录用户（对齐 ai-service UserResponse：{ user_id, email, nickname, status }） */
export interface User {
  userId: number
  email: string
  nickname: string
  status?: number // 1=正常 2=禁用 3=待激活
  avatarUrl?: string
  globalRole?: number
  globalRoleDesc?: string
  tenantId?: number
  tenantName?: string
  kbCount?: number
  lastLoginAt?: string
}

export interface LoginRequest {
  email: string
  password: string
}

export interface LoginResponse {
  accessToken: string
  refreshToken: string
  expiresIn: number
  user: User
}
