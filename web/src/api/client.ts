export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly detail: unknown,
  ) {
    super(message)
  }
}

function toCamel(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => toCamel(item))
  }
  if (!value || typeof value !== 'object') {
    return value
  }
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).map(([key, item]) => [
      key.replace(/_([a-z])/g, (_, char: string) => char.toUpperCase()),
      toCamel(item),
    ]),
  )
}

function toSnake(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => toSnake(item))
  }
  if (!value || typeof value !== 'object') {
    return value
  }
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).map(([key, item]) => [
      key.replace(/[A-Z]/g, (char) => `_${char.toLowerCase()}`),
      toSnake(item),
    ]),
  )
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  let body = init.body
  if (body && typeof body !== 'string') {
    headers.set('Content-Type', 'application/json')
    body = JSON.stringify(toSnake(body))
  }
  // 始终取最新：避免浏览器/中间层缓存导致更新后仍读到旧响应。
  const response = await fetch(path, { ...init, cache: 'no-store', headers, body })
  const text = await response.text()
  let data: unknown = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = text
    }
  }
  if (!response.ok) {
    let message = response.statusText
    if (typeof data === 'string') {
      message = data
    } else if (data && typeof data === 'object') {
      const detail = (data as { detail?: unknown }).detail
      if (typeof detail === 'string') {
        message = detail
      } else if (Array.isArray(detail)) {
        message = detail.map((item) => (
          typeof item === 'string' ? item : JSON.stringify(item)
        )).join('；')
      }
    }
    throw new ApiError(message, response.status, data)
  }
  return toCamel(data) as T
}
