import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiError, api, getToken, setToken, setUnauthorizedHandler } from "../api/client";
import type { CompanyBrief, Dictionaries, Me } from "../api/types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  /** вход сохранён, но банк не ответил (сеть, сбой сервера) — это не выход из кабинета */
  unreachable: boolean;
  /** в этой вкладке уже входили: после обычного выхода страница входа не возвращает на прежний адрес */
  hadSession: boolean;
  retry: () => void;
  company: CompanyBrief | null;
  setCompanyId: (id: string) => void;
  login: (login: string, password: string) => Promise<Me>;
  logout: () => void;
  dict: Dictionaries | null;
}

const AuthContext = createContext<AuthState | null>(null);
const COMPANY_KEY = "scc.company";
const EXPIRED_KEY = "scc.expired";

/** Сессия закончилась сама (ответ 401), а не клиент вышел: страница входа скажет об этом один раз. */
export function sessionExpired(): boolean {
  try {
    return window.sessionStorage.getItem(EXPIRED_KEY) === "1";
  } catch {
    return false;
  }
}
export function forgetSessionExpired(): void {
  try {
    window.sessionStorage.removeItem(EXPIRED_KEY);
  } catch {
    /* не критично */
  }
}

function readCompany(): string | null {
  try {
    return window.localStorage.getItem(COMPANY_KEY);
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [hasToken, setHasToken] = useState(Boolean(getToken()));
  const [hadSession, setHadSession] = useState(hasToken);
  const [companyId, setCompanyIdState] = useState<string | null>(readCompany());

  const meQuery = useQuery({
    queryKey: ["me"],
    queryFn: () => api<Me>("/api/v1/auth/me"),
    enabled: hasToken,
    // сбой сети или сервера — повторяем; на 401 api() сам сбрасывает вход
    retry: (count, e) => !(e instanceof ApiError && e.status === 401) && count < 2,
  });
  const dictQuery = useQuery({
    queryKey: ["dict"],
    queryFn: () => api<Dictionaries>("/api/v1/dictionaries"),
    enabled: hasToken && meQuery.isSuccess,
    staleTime: 60_000,
  });

  const logout = useCallback(() => {
    setToken(null);
    setHasToken(false);
    qc.clear();
  }, [qc]);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      try {
        window.sessionStorage.setItem(EXPIRED_KEY, "1");
      } catch {
        /* не критично */
      }
      logout();
    });
  }, [logout]);

  const login = useCallback(
    async (loginValue: string, password: string) => {
      const res = await api<{ access_token: string }>("/api/v1/auth/login", {
        method: "POST",
        json: { login: loginValue, password },
      });
      setToken(res.access_token);
      qc.clear();
      setHasToken(true);
      setHadSession(true);
      const me = await qc.fetchQuery({ queryKey: ["me"], queryFn: () => api<Me>("/api/v1/auth/me") });
      return me;
    },
    [qc],
  );

  const setCompanyId = useCallback((id: string) => {
    setCompanyIdState(id);
    try {
      window.localStorage.setItem(COMPANY_KEY, id);
    } catch {
      /* не критично */
    }
  }, []);

  const value = useMemo<AuthState>(() => {
    const me = hasToken ? (meQuery.data ?? null) : null;
    const companies = me?.companies ?? [];
    const company = companies.find((c) => c.id === companyId) ?? companies[0] ?? null;
    const authError = meQuery.error instanceof ApiError && meQuery.error.status === 401;
    return {
      me,
      loading: hasToken && meQuery.isLoading,
      // только когда данных о входе ещё нет: если фоновое обновление не прошло, открытая страница с правками остаётся на месте
      unreachable: hasToken && !meQuery.data && meQuery.isError && !authError,
      hadSession,
      retry: () => void meQuery.refetch(),
      company,
      setCompanyId,
      login,
      logout,
      dict: dictQuery.data ?? null,
    };
  }, [hasToken, hadSession, meQuery, companyId, setCompanyId, login, logout, dictQuery.data]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
