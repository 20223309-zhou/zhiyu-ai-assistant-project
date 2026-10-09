/**
 * API 统一出口。页面只从这里引入，不直接依赖后端细节。
 *
 * 使用方式：
 *   import { login, register } from '@/api'
 *   import { listFiles, getTags } from '@/api'
 */

export { ApiError, api, getToken, setToken, clearToken, upload } from './request'

export * from './constants'
export * from './mappers'

export * from './auth'
export * from './conversation'
export * from './knowledge'
