import { useQuery } from "@tanstack/react-query";
import { LogIn } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { DemoAccount } from "../api/types";
import { useAuth } from "../lib/auth";
import { errorText } from "../lib/toast";

const STEPS = [
  ["Задача", "Расскажите, на что нужны деньги — подберём подходящий кредит"],
  ["Заявка", "Данные компании уже есть у банка: заполнять почти ничего не нужно"],
  ["Проверка", "Банк сам запросит сведения в ЕГРЮЛ, ФНС и бюро кредитных историй"],
  ["Предложение", "Понятные условия и объяснение, почему они такие"],
  ["Подпись и выдача", "Подпись кодом в кабинете — деньги приходят на расчётный счёт"],
];

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
          <span className="brand-mark" aria-hidden>
            <span />
          </span>
          Умный кредитный кабинет
        </div>
        <h1>Кредит для бизнеса — от задачи до денег на счёте</h1>
        <p>
          Прототип цифрового кредитного конвейера для малого и микробизнеса. Предприниматель проходит весь путь в одном кабинете, а
          банк обрабатывает заявку в том же процессе: автоматически, когда это безопасно, и вместе со специалистом, когда нужна
          экспертиза.
        </p>
        <div className="login-steps">
          {STEPS.map(([t, d], i) => (
            <div key={t}>
              <b>{i + 1}</b>
              <span>
                <span style={{ color: "#fff", fontWeight: 600 }}>{t}.</span> {d}
              </span>
            </div>
          ))}
        </div>
      </aside>
      <section className="login-main">
        <div>
          <h2>Вход</h2>
          <p className="muted small" style={{ marginTop: 6 }}>
            В рабочей версии кабинет открывается из интернет-банка для бизнеса без отдельного входа. Здесь — демо-учётки.
          </p>
        </div>
        <form className="stack" onSubmit={onSubmit} style={{ maxWidth: 420 }}>
          <div className="field">
            <label htmlFor="login">Логин</label>
            <input id="login" className="input" value={form.login} onChange={(e) => setForm({ ...form, login: e.target.value })} autoComplete="username" />
          </div>
          <div className="field">
            <label htmlFor="password">Пароль</label>
            <input
              id="password"
              type="password"
              className="input"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              autoComplete="current-password"
            />
          </div>
          {error && <div className="callout bad">{error}</div>}
          <button className="btn btn-primary" disabled={busy || !form.login || !form.password}>
            <LogIn size={18} /> Войти
          </button>
        </form>

        {demo.data && (
          <div className="stack">
            <div>
              <h3>Предприниматели</h3>
              <p className="muted small">У каждой компании свой сценарий рассмотрения. Пароль у всех: demo1234</p>
            </div>
            <div className="stack-s">
              {clients.map((a) => (
                <button key={a.login} className="account" disabled={busy} onClick={() => doLogin(a.login, a.password)}>
                  <span className="who">{a.full_name}</span>
                  <span className="faint xs">{a.login}</span>
                  <span className="what">{a.title}</span>
                  <span className="scen">{a.scenario}</span>
                </button>
              ))}
            </div>
            <h3 style={{ marginTop: 8 }}>Сотрудники банка</h3>
            <div className="stack-s">
              {staff.map((a) => (
                <button key={a.login} className="account" disabled={busy} onClick={() => doLogin(a.login, a.password)}>
                  <span className="who">{a.full_name}</span>
                  <span className="faint xs">{a.login}</span>
                  <span className="what">{a.title}</span>
                  <span className="scen">{a.scenario}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
