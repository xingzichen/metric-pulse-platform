/**
 * 浏览器端统一 HTTP 边界。
 *
 * 所有页面都经此处携带会话 Cookie、序列化 JSON，并把后端错误归一化为 ApiError，
 * 从而避免每个页面分别实现一套容易不一致的鉴权和错误处理。
 */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await fetch(path, {
    ...init,
    headers,
    // 后端使用 HttpOnly 会话 Cookie；显式 include 才能让同源/代理请求携带登录态。
    credentials: "include",
  });
  if (!response.ok) {
    const payload = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    throw new ApiError(response.status, payload.detail ?? "请求失败");
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
