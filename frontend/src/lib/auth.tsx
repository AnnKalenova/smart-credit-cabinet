import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, getToken, setToken, setUnauthorizedHandler } from "../api/client";
import type { CompanyBrief, Dictionaries, Me } from "../api/types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  company: CompanyBrief | null;
  setCompanyId: (id: string) => void;
  login: (login: string, password: string) => Promise<Me>;
  logout: () => void;
  dict: Dictionaries | null;
}

const AuthContext = createContext<AuthState | null>(null);
const COMPANY_KEY = "scc.company";

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
  const [companyId, setCompanyIdState] = useState<string | null>(readCompany());

  const meQuery = useQuery({
    queryKey: ["me"],
    queryFn: () => api<Me>("/api/v1/auth/me"),
    enabled: hasToken,
    retry: false,
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
    setUnauthorizedHandler(logout);
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
    return {
      me,
      loading: hasToken && meQuery.isLoading,
      company,
      setCompanyId,
      login,
      logout,
      dict: dictQuery.data ?? null,
    };
  }, [hasToken, meQuery.data, meQuery.isLoading, companyId, setCompanyId, login, logout, dictQuery.data]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
