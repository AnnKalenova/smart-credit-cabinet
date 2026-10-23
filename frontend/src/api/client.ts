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

/** Сообщение по коду ответа, если сервер не прислал понятного текста (например, страница ошибки прокси). */
function fallbackMessage(status: number): string {
  if (status === 413) return "Файл больше 10 МБ — уменьшите его или приложите в другом формате";
  if (status === 429) return "Слишком много запросов — подождите немного и повторите";
  if (status >= 500) return "Сервис временно недоступен, попробуйте ещё раз через минуту";
  return `Не удалось выполнить запрос (код ${status})`;
}

function parseError(status: number, body: unknown): ApiError {
  const b = body as { detail?: unknown; request_id?: string } | undefined;
  const detail = b?.detail;
  if (typeof detail === "string") {
    // для непредвиденных ошибок сервер отдаёт идентификатор запроса — по нему ошибку найдут в логах
    return new ApiError(status, b?.request_id ? `${detail} (код запроса ${b.request_id.slice(0, 8)})` : detail);
  }
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const d = detail as { message?: string; problems?: string[] };
    return new ApiError(status, d.message ?? fallbackMessage(status), d.problems ?? []);
  }
  if (Array.isArray(detail)) {
    const msgs = detail.map((e: { msg?: string; loc?: unknown[] }) => `${(e.loc ?? []).slice(-1)[0] ?? ""}: ${e.msg ?? ""}`);
    return new ApiError(status, "Проверьте введённые данные", msgs);
  }
  return new ApiError(status, fallbackMessage(status));
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
    throw new ApiError(0, "Нет связи с банком — проверьте интернет и попробуйте ещё раз");
  }
  if (res.status === 401 && token) {
    onUnauthorized?.();
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown = undefined;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      // не JSON: например, HTML-страница ошибки прокси при перезапуске сервиса
      if (!res.ok) throw new ApiError(res.status, fallbackMessage(res.status));
      throw new ApiError(res.status, "Сервер вернул неожиданный ответ — обновите страницу");
    }
  }
  if (!res.ok) throw parseError(res.status, data);
  return data as T;
}

export async function download(path: string, fileName: string): Promise<void> {
  let res: Response;
  try {
    res = await fetch(path, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  } catch {
    throw new ApiError(0, "Не удалось скачать файл: нет связи с банком");
  }
  if (!res.ok) throw new ApiError(res.status, "Не удалось скачать файл — попробуйте ещё раз");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  a.click();
  URL.revokeObjectURL(url);
}
