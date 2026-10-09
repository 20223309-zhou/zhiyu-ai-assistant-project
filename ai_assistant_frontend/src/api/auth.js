/**
 * 用户 / 鉴权接口，对应后端 main.py 与 auth.py。
 *
 * 后端接口清单：
 *  POST /users/register  请求体 { username, account, password }
 *  POST /users/login     请求体 { account, password } -> data { token, token_type, user }
 *  GET  /users/me        Authorization: Bearer <token> -> data UserVO
 *  POST /users/logout    使当前用户已签发的 token 全部失效
 */

import { api, setToken, clearToken } from './request'

/**
 * 后端 UserVO -> 前端用户对象。
 * 后端 UserVO 只有 { id, username, account, bio, role }：
 *  - 后端 username 语义是「账户名/昵称」，映射到前端 name 用于展示
 *  - 后端没有 email / dept 字段，前端不再收集这两项
 */
export function mapUser(vo) {
  if (!vo) return null
  return {
    id: vo.id,
    name: vo.username || vo.account || '用户',
    username: vo.account || vo.username || '',
    account: vo.account || '',
    bio: vo.bio || '',
    role: vo.role || 'user'
  }
}

/** 登录：前端表单用 username 字段承载「登录账号」，这里映射为后端的 account */
export async function login({ account, password }) {
  const data = await api('/users/login', {
    method: 'POST',
    body: { account, password }
  })
  setToken(data?.token)
  return { ...mapUser(data?.user), token: data?.token }
}

/**
 * 注册。前端表单只收集后端 UserRegisterDto 需要的三项：
 * 用户名（映射到后端 username）、账号（映射到后端 account）、密码。
 * 后端 users 表没有 email / dept 字段，前端也不再收集。
 */
export async function register({ name, account, password }) {
  return api('/users/register', {
    method: 'POST',
    body: {
      username: name,
      account,
      password
    }
  })
}

/** 校验登录态 / 拉取当前用户 */
export async function fetchMe() {
  const vo = await api('/users/me')
  return mapUser(vo)
}

/** 退出登录：后端会把 token_version +1，使已签发的 token 立即失效 */
export async function logout() {
  try {
    await api('/users/logout', { method: 'POST' })
  } finally {
    clearToken()
  }
}
