const TOKEN_KEY = "scc.token";

let token: string | null = null;
try {
  token = window.localStorage.getItem(TOKEN_KEY);
} catch {
  token = null;
}

export function getToken(): string | null {
  return token;
}

export function setToken(value: string | null): void {
  token = value;
  try {
    if (value) window.localStorage.setItem(TOKEN_KEY, value);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* хранилище недоступно — сессия останется в памяти вкладки */
  }
}

export class ApiError extends Error {
  status: number;
  problems: string[];

  constructor(status: number, message: string, problems: string[] = []) {
    super(message);
    this.status = status;
    this.problems = problems;
  }
}

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn;
}

function parseError(status: number, body: unknown): ApiError {
  const detail = (body as { detail?: unknown })?.detail;
  if (typeof detail === "string") return new ApiError(status, detail);
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const d = detail as { message?: string; problems?: string[] };
    return new ApiError(status, d.message ?? "Ошибка запроса", d.problems ?? []);
  }
  if (Array.isArray(detail)) {
    const msgs = detail.map((e: { msg?: string; loc?: unknown[] }) => `${(e.loc ?? []).slice(-1)[0] ?? ""}: ${e.msg ?? ""}`);
    return new ApiError(status, "Проверьте введённые данные", msgs);
  }
  return new ApiError(status, status >= 500 ? "Сервис временно недоступен, попробуйте ещё раз" : "Ошибка запроса");
}

export async function api<T = unknown>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  let res: Response;
  try {
    res = await fetch(path, { ...init, headers, body });
  } catch {
    throw new ApiError(0, "Нет связи с сервером — проверьте, что сервис запущен");
  }
  if (res.status === 401 && token) {
    onUnauthorized?.();
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  const data = text ? JSON.parse(text) : undefined;
  if (!res.ok) throw parseError(res.status, data);
  return data as T;
}

export async function download(path: string, fileName: string): Promise<void> {
  const res = await fetch(path, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) throw new ApiError(res.status, "Не удалось скачать файл");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  a.click();
  URL.revokeObjectURL(url);
}
