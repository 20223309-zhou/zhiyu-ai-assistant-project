/**
 * 会话与对话接口，对应后端 main.py。
 *
 * 后端接口清单：
 *  POST /conversation/add             请求体 { title } -> data { id, user_id, title, created_at, updated_at }
 *  POST /conversation/chat            请求体 { query, session_id(UUID), rag_flag, model_name } -> data: 回答文本
 *  POST /conversation/chat/stream     同上请求体，返回 SSE（event: start / token / error / done）
 *  GET  /conversation/list            当前用户的会话列表（按创建时间倒序）-> data [ConversationVO]
 *  GET  /conversation/messages/list   某会话的消息（按 sequence 升序）-> data [ConversationMessageVO]
 *                                     参数 conversation_id=<UUID>
 *  DELETE /conversation/{id}          删除会话及其全部消息（不可恢复）
 *  GET  /model/get                    可用模型清单
 *
 * 注意：/conversation/chat 要求 session_id 必须是已存在、属于当前用户且未软删除的会话，
 * 因此前端必须先调 /conversation/add 拿到 id，再发起对话。
 */

import { ApiError, BASE_URL, api, getToken } from './request'
import { mapConversation, mapMessage } from './mappers'

/** 新建会话，返回 { id, user_id, title, created_at, updated_at } */
export async function createConversation(title = '新的对话') {
  return api('/conversation/add', {
    method: 'POST',
    body: { title }
  })
}

/**
 * 发起提问（一次性返回，非流式），返回模型回答的纯文本。
 * 对话页现在走 streamChat()，这个留给不需要流式的调用方。
 * @param {{ sessionId: string, query: string, ragFlag?: boolean }} payload
 */
export async function sendMessage({ sessionId, query, ragFlag = false }) {
  const content = await api('/conversation/chat', {
    method: 'POST',
    body: { session_id: sessionId, query, rag_flag: ragFlag }
  })
  return typeof content === 'string' ? content : String(content ?? '')
}

/** 当前用户的会话列表（后端已按 created_at 倒序，前端不再重排） */
export async function listConversations() {
  const data = await api('/conversation/list')
  return (data || []).map(mapConversation)
}

/** 指定会话的历史消息（按 sequence 升序，即对话发生顺序） */
export async function listMessages(conversationId) {
  const data = await api('/conversation/messages/list', {
    params: { conversation_id: conversationId }
  })
  return (data || []).map(mapMessage)
}

/**
 * 删除会话：连同该会话的全部消息一起删除，不可恢复。
 * 后端只是本人的会话才能删，别人的会话 id 会返回 404。
 * @returns {{ id: string, deletedMessages: number, checkpointCleared: boolean }}
 */
export async function deleteConversation(conversationId) {
  const data = await api(`/conversation/${conversationId}`, { method: 'DELETE' })
  return {
    id: data?.id || conversationId,
    deletedMessages: Number(data?.deleted_messages) || 0,
    // 后端清理 langgraph checkpoint 失败时为 false（会话本身仍已删除）
    checkpointCleared: data?.checkpoint_cleared !== false
  }
}

/**
 * 可用模型清单。后端在 agent/chat_model.py 维护，前端不要自己硬编码一份，
 * 否则以后加模型两边会不一致。默认项用 isDefault 标记。
 * @returns {Promise<Array<{ name: string, label: string, isDefault: boolean }>>}
 */
export async function getModels() {
  const data = await api('/model/get')
  return (data || [])
    .map((m) => ({
      name: m?.name || '',
      label: m?.label || m?.name || '',
      isDefault: Boolean(m?.is_default)
    }))
    .filter((m) => m.name)
}

/**
 * 流式对话。用 fetch + ReadableStream 手工解析 SSE。
 *
 * 为什么不用 EventSource：它只支持 GET，而且没法带 Authorization 头，
 * 所以带 Bearer token 的流式请求只能这么写。
 *
 * @param {{ sessionId: string, query: string, ragFlag?: boolean,
 *           modelName?: string, signal?: AbortSignal }} payload
 * @param {{ onStart?, onToken?, onDone?, onError? }} handlers
 * @returns {Promise<string>} 拼接后的完整回答
 */
export async function streamChat(
  { sessionId, query, ragFlag = false, modelName, signal },
  handlers = {}
) {
  const response = await fetch(`${BASE_URL}/conversation/chat/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${getToken()}`
    },
    body: JSON.stringify({
      session_id: sessionId,
      query,
      rag_flag: ragFlag,
      model_name: modelName
    }),
    signal
  })

  if (!response.ok) {
    let detail = ''
    try {
      detail = (await response.json())?.detail ?? ''
    } catch (e) {
      // 响应体不是 JSON（比如网关返回的 502 页面），下面走兜底文案
    }
    throw new ApiError(
      typeof detail === 'string' && detail ? detail : `请求失败（HTTP ${response.status}）`,
      response.status
    )
  }
  if (!response.body) throw new ApiError('当前浏览器不支持流式响应', 0)

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let answer = ''

  function handleFrame(frame) {
    let event = 'message'
    const dataLines = []
    frame.split('\n').forEach((line) => {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
    })
    if (!dataLines.length) return

    let payload = {}
    try {
      payload = JSON.parse(dataLines.join('\n'))
    } catch (e) {
      return // 半截帧，丢弃
    }

    if (event === 'start') {
      handlers.onStart?.(payload)
    } else if (event === 'token') {
      const text = payload.t || ''
      answer += text
      handlers.onToken?.(text)
    } else if (event === 'done') {
      handlers.onDone?.(payload)
    } else if (event === 'error') {
      handlers.onError?.(new ApiError(payload.message || '生成失败，请稍后重试', 0))
    }
  }

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // SSE 用空行分帧。行的解析里已经 trim 过，所以 \r\n 不用单独处理
    let index
    while ((index = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, index)
      buffer = buffer.slice(index + 2)
      if (frame.trim()) handleFrame(frame)
    }
  }
  // 有些服务端最后一帧可能没有以空行收尾
  if (buffer.trim()) handleFrame(buffer)

  return answer
}
