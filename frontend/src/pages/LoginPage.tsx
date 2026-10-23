import { useQuery } from "@tanstack/react-query";
import { LogIn } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { DemoAccount } from "../api/types";
import { Brand } from "../components/Layout";
import { useAuth } from "../lib/auth";
import { errorText } from "../lib/toast";

const STEPS = [
  ["Задача", "расскажите, на что нужны деньги, — подберём подходящий кредит"],
  ["Заявка", "данные компании уже есть у банка, заполнять почти ничего не нужно"],
  ["Проверка", "банк сам запросит сведения в ЕГРЮЛ, ФНС и бюро кредитных историй"],
  ["Предложение", "понятные условия и объяснение, почему они такие"],
  ["Подпись и выдача", "подпись кодом в кабинете, деньги — на расчётном счёте"],
];

function AccountCard({ a, disabled, onPick }: { a: DemoAccount; disabled: boolean; onPick: (a: DemoAccount) => void }) {
  return (
    <button className="account" disabled={disabled} onClick={() => onPick(a)}>
      <span className="scen">{a.scenario_title ?? a.title}</span>
      <span className="what">{a.scenario_hint ?? a.scenario}</span>
      <span className="who">
        {a.full_name}, {a.login}
      </span>
    </button>
  );
}

export function LoginPage() {
  const { me, login } = useAuth();
  const nav = useNavigate();
  const [form, setForm] = useState({ login: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const demo = useQuery({
    queryKey: ["demo-accounts"],
    queryFn: () => api<DemoAccount[]>("/api/v1/auth/demo-accounts"),
    retry: false,
  });

  if (me) return <Navigate to={me.user.role === "CLIENT" ? "/c" : "/b"} replace />;

  const doLogin = async (l: string, p: string) => {
    setBusy(true);
    setError(null);
    try {
      const who = await login(l, p);
      nav(who.user.role === "CLIENT" ? "/c" : "/b", { replace: true });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void doLogin(form.login, form.password);
  };

  const clients = (demo.data ?? []).filter((a) => a.role === "CLIENT");
  const staff = (demo.data ?? []).filter((a) => a.role !== "CLIENT");

  return (
    <div className="login">
      <aside className="login-side">
        <div className="brand">
          <Brand title="Кредитный кабинет" subtitle="для малого бизнеса" />
        </div>
        <h1>Кредит для бизнеса — от задачи до денег на счёте</h1>
        <p>
          Весь путь — в одном кабинете. Банк рассматривает заявку сразу: автоматически, когда это безопасно, и со специалистом, когда
          нужна экспертиза.
        </p>
        <div className="login-steps">
          {STEPS.map(([t, d], i) => (
            <div key={t}>
              <b>{i + 1}</b>
              <span>
                <b>{t}:</b> {d}
              </span>
            </div>
          ))}
        </div>
      </aside>
      <section className="login-main">
        <div>
          <h2>Вход</h2>
          <p className="muted" style={{ marginTop: 6 }}>
            Войдите как предприниматель или как сотрудник банка.
          </p>
        </div>
        <form className="stack" onSubmit={onSubmit} style={{ maxWidth: 420 }}>
          <div className="field">
            <label htmlFor="login">Логин</label>
            <input id="login" className="input" maxLength={64} value={form.login} onChange={(e) => setForm({ ...form, login: e.target.value })} autoComplete="username" />
          </div>
          <div className="field">
            <label htmlFor="password">Пароль</label>
            <input
              id="password"
              type="password"
              className="input"
              maxLength={128}
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              autoComplete="current-password"
            />
          </div>
          {error && <div className="callout bad">{error}</div>}
          <button className="btn btn-signal" disabled={busy || !form.login || !form.password}>
            <LogIn size={18} /> Войти
          </button>
        </form>

        {demo.data && (
          <div className="stack">
            <div>
              <h3>Войти в один клик</h3>
              <p className="muted small" style={{ marginTop: 4 }}>
                У каждой компании свой путь заявки — от одобрения за секунды до решения специалиста. Пароль у всех учётных записей — demo1234.
              </p>
            </div>
            <div className="accounts">
              {clients.map((a) => (
                <AccountCard key={a.login} a={a} disabled={busy} onPick={(x) => doLogin(x.login, x.password)} />
              ))}
            </div>
            <h3 style={{ marginTop: 8 }}>Сотрудники банка</h3>
            <div className="accounts">
              {staff.map((a) => (
                <AccountCard key={a.login} a={a} disabled={busy} onPick={(x) => doLogin(x.login, x.password)} />
              ))}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
