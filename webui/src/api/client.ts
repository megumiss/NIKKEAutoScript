import { beginEntryChange, checkEntry, entryRequired, requireEntry } from './security'

export async function request(path: string, init: RequestInit = {}) {
  if (entryRequired.value && path !== '/api/system/status') throw new Error('请使用最新的完整安全入口重新连接。')
  const changing = path === '/api/security/entry/regenerate' || (path === '/api/system/deploy' && init.method === 'PATCH' && String(init.body).includes('"SecurityEntryEnabled"'))
  const finish = changing ? beginEntryChange() : undefined
  try {
    const response = await fetch(path, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init.headers || {}) },
    })
    const body = await response.json().catch(() => ({}))
    if (response.status === 401 || body.security_entry?.authorized === false) {
      // An old in-flight health request/WS may finish before the new Cookie is
      // installed. Wait for our own change response before declaring a lockout.
      if (!changing && await checkEntry().catch(() => false)) throw new Error('访问凭据刚刚更新，请重试当前操作。')
      requireEntry()
      throw new Error('安全入口已开启或已更新，请使用最新的完整入口重新连接。')
    }
    if (!response.ok) {
      const error = new Error(body.error || body.message || `Request failed (${response.status})`) as Error & { code?: string; errors?: Record<string, string> }
      if (body.code) error.code = body.code
      // 批量校验接口（如 schedule/save）在 422 时携带按任务分组的错误明细，透传给页面标红对应行。
      if (body.errors) error.errors = body.errors
      throw error
    }
    return body
  } finally { finish?.() }
}

export const api = {
  get: (path: string) => request(path),
  post: (path: string, body: unknown = {}) => request(path, { method: 'POST', body: JSON.stringify(body) }),
  patch: (path: string, body: unknown) => request(path, { method: 'PATCH', body: JSON.stringify(body) }),
  del: (path: string) => request(path, { method: 'DELETE' }),
}
