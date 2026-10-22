import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, LogOut } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Notification } from "../api/types";
import { useAuth } from "../lib/auth";
import { dateTime } from "../lib/format";

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
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);
  const unread = data?.unread ?? 0;
  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        className="icon-btn"
        aria-label={`Уведомления${unread ? `: ${unread} новых` : ""}`}
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
            <span className="faint xs">Все события по заявкам — здесь, без SMS и почты</span>
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

export function Layout({ children, area }: { children: ReactNode; area: "client" | "bank" }) {
  const { me, company, setCompanyId, logout, dict } = useAuth();
  const nav = useNavigate();
  if (!me) return null;
  const isClient = area === "client";
  return (
    <>
      {dict?.demo_mode && (
        <div className="demo-strip">
          Прототип для хакатона: данные синтетические, внешние системы банка заменены симуляторами
        </div>
      )}
      <header className="topbar">
        <div className="topbar-inner">
          <Link to={isClient ? "/c" : "/b"} className="brand">
            <span className="brand-mark" aria-hidden>
              <span />
            </span>
            {isClient ? "Кредитный кабинет" : "Кредитный конвейер"}
            <small>{isClient ? "для бизнеса" : "рабочее место банка"}</small>
          </Link>
          <nav className="topnav" aria-label="Разделы">
            {isClient ? (
              <>
                <NavLink to="/c" end>
                  Главная
                </NavLink>
                <NavLink to="/c/match">Подобрать кредит</NavLink>
              </>
            ) : (
              <>
                <NavLink to="/b" end>
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
                onClick={() => {
                  logout();
                  nav("/login");
                }}
              >
                <LogOut size={18} />
              </button>
            </div>
          </div>
        </div>
      </header>
      <main className="page">{children}</main>
    </>
  );
}
