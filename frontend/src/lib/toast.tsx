import { CircleAlert, CircleCheck } from "lucide-react";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { ApiError } from "../api/client";

interface Toast {
  id: number;
  text: string;
  kind: "ok" | "error";
}

interface ToastApi {
  ok: (text: string) => void;
  error: (e: unknown) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

export function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    return e.problems.length ? `${e.message}: ${e.problems.join("; ")}` : e.message;
  }
  // готовый текст из самого интерфейса (например, проверка размера файла) показываем как есть
  if (typeof e === "string" && e) return e;
  return "Что-то пошло не так. Обновите страницу и попробуйте снова.";
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, kind: Toast["kind"]) => {
    const id = Date.now() + Math.random();
    setItems((prev) => [...prev, { id, text, kind }]);
    window.setTimeout(() => setItems((prev) => prev.filter((t) => t.id !== id)), kind === "error" ? 7000 : 3500);
  }, []);
  const apiValue: ToastApi = {
    ok: (text) => push(text, "ok"),
    error: (e) => push(errorText(e), "error"),
  };
  return (
    <ToastContext.Provider value={apiValue}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.kind === "error" ? "error" : ""}`}>
            {t.kind === "error" ? <CircleAlert size={18} /> : <CircleCheck size={18} />}
            <span>{t.text}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast outside ToastProvider");
  return ctx;
}
