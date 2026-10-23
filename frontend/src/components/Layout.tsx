import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, LogOut } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { Notification } from "../api/types";
import { useAuth } from "../lib/auth";
import { dateTime } from "../lib/format";
import { savePending } from "../lib/pendingSaves";
import { useToast } from "../lib/toast";

function Notifications() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const { data } = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api<{ unread: number; items: Notification[] }>("/api/v1/notifications"),
    refetchInterval: 5000,
  });
  const readAll = useMutation({
    mutationFn: () => api("/api/v1/notifications/read-all", { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });
  const button = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    // Escape закрывает список и возвращает фокус на колокольчик
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && ref.current?.querySelector(".dropdown")) {
        setOpen(false);
        button.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, []);
  const unread = data?.unread ?? 0;
  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        ref={button}
        className="icon-btn"
        aria-label={`Уведомления${unread ? `: ${unread} новых` : ""}`}
        aria-expanded={open}
        onClick={() => {
          setOpen((o) => !o);
          if (!open && unread) readAll.mutate();
        }}
      >
        <Bell size={20} />
        {unread > 0 && <span className="badge-dot">{unread}</span>}
      </button>
      {open && (
        <div className="dropdown">
          <div className="dropdown-head">
            <b>Уведомления</b>
            <span className="faint xs">События по заявкам компании</span>
          </div>
          <div style={{ maxHeight: 380, overflow: "auto" }}>
            {(data?.items ?? []).length === 0 && <p className="empty small">Новых событий нет</p>}
            {(data?.items ?? []).map((n) => (
              <a
                key={n.id}
                href="#"
                className={`note ${n.is_read ? "" : "unread"}`}
                onClick={(e) => {
                  e.preventDefault();
                  setOpen(false);
                  if (n.application_id) nav(`/c/applications/${n.application_id}`);
                }}
              >
                <b>{n.title}</b>
                <div className="muted">{n.body}</div>
                <time>{dateTime(n.created_at)}</time>
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** Знак кабинета: три ступени «конвейера» на красной плашке. */
export function Brand({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <>
      <span className="brand-mark" aria-hidden>
        <span />
      </span>
      <span>
        {title}
        <small>{subtitle}</small>
      </span>
    </>
  );
}

export function Layout({ children, area }: { children: ReactNode; area: "client" | "bank" }) {
  const { me, company, setCompanyId, logout } = useAuth();
  const nav = useNavigate();
  const toast = useToast();
  const { pathname } = useLocation();
  const isClient = area === "client";
  // во вкладке браузера видно, какой это кабинет
  useEffect(() => {
    document.title = isClient ? "Кредитный кабинет для бизнеса" : "Кредитный конвейер — рабочее место банка";
  }, [isClient]);
  if (!me) return null;
  // страница заявки — часть раздела «Главная» у клиента и «Заявки» у банка
  const inApps = (root: string) => ({ isActive }: { isActive: boolean }) => (isActive || pathname.startsWith(`${root}/applications/`) ? "active" : "");
  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <Link to={isClient ? "/c" : "/b"} className="brand" aria-label={isClient ? "Кредитный кабинет, главная" : "Кредитный конвейер, заявки"}>
            <Brand title={isClient ? "Кредитный кабинет" : "Кредитный конвейер"} subtitle={isClient ? "для бизнеса" : "рабочее место банка"} />
          </Link>
          <nav className="topnav" aria-label="Разделы">
            {isClient ? (
              <>
                <NavLink to="/c" end className={inApps("/c")}>
                  Главная
                </NavLink>
                <NavLink to="/c/match">Подобрать кредит</NavLink>
              </>
            ) : (
              <>
                <NavLink to="/b" end className={inApps("/b")}>
                  Заявки
                </NavLink>
                <NavLink to="/b/dashboard">Мониторинг</NavLink>
                <NavLink to="/b/settings">Параметры политики</NavLink>
              </>
            )}
          </nav>
          <div className="topbar-right">
            {isClient && me.companies.length > 1 && (
              <select className="select company-select" value={company?.id} onChange={(e) => setCompanyId(e.target.value)} aria-label="Компания">
                {me.companies.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.short_name}
                  </option>
                ))}
              </select>
            )}
            {isClient && <Notifications />}
            <div className="user-chip">
              <div className="who">
                <span>{me.user.full_name}</span>
                <span>{isClient ? `${company?.position_label ?? ""}, ${company?.short_name ?? ""}` : me.user.position_title}</span>
              </div>
              <button
                className="icon-btn"
                aria-label="Выйти"
                title="Выйти"
                onClick={async () => {
                  try {
                    await savePending();
                    logout();
                    nav("/login");
                  } catch {
                    toast.error(new ApiError(0, "Выход отменён: изменения в черновике ещё не сохранены. Проверьте связь и попробуйте ещё раз."));
                  }
                }}
              >
                <LogOut size={18} />
              </button>
            </div>
          </div>
        </div>
      </header>
      <main id="main" tabIndex={-1} className="page">
        {children}
      </main>
    </>
  );
}
