export async function request(path: string, init: RequestInit = {}) {
  const response = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init.headers || {}) },
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = new Error(body.error || body.message || `Request failed (${response.status})`) as Error & { code?: string }
    if (body.code) error.code = body.code
    throw error
  }
  return body
}

export const api = {
  get: (path: string) => request(path),
  post: (path: string, body: unknown = {}) => request(path, { method: 'POST', body: JSON.stringify(body) }),
  patch: (path: string, body: unknown) => request(path, { method: 'PATCH', body: JSON.stringify(body) }),
  del: (path: string) => request(path, { method: 'DELETE' }),
}
