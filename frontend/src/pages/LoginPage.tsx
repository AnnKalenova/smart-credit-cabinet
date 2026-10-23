import { useQuery } from "@tanstack/react-query";
import { Check, CirclePlus, LogIn } from "lucide-react";
import { useEffect, useState, type CSSProperties, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { DemoAccount } from "../api/types";
import { Brand } from "../components/Layout";
import { forgetSessionExpired, sessionExpired, useAuth } from "../lib/auth";
import { money, pct } from "../lib/format";
import { errorText, useToast } from "../lib/toast";

const ROUTE = ["Задача", "Заявка", "Проверка", "Предложение", "Подпись и выдача"];
const OFFER_STEP = 3;

/** Путь заявки и решение, которое клиент видит на шаге «Предложение». Цифры — из демо-сценария
 *  «Автоматическое одобрение» (ООО «Северный хлеб», вход «smirnov») при ставках по умолчанию. */
function Showcase() {
  return (
    <figure className="login-show">
      <ol className="login-route" aria-label="Путь заявки">
        {ROUTE.map((title, i) => (
          <li
            key={title}
            className={i < OFFER_STEP ? "done" : i === OFFER_STEP ? "current" : undefined}
            aria-current={i === OFFER_STEP ? "step" : undefined}
            style={{ "--i": i } as CSSProperties}
          >
            <span className="node" aria-hidden>
              {i < OFFER_STEP && <Check size={14} strokeWidth={3} />}
            </span>
            {title}
          </li>
        ))}
      </ol>
      <div className="login-sample">
        <span className="login-sample-status">Одобрено автоматически</span>
        <p className="login-sample-amount">
          {money(3_000_000)} <small>на 12 мес.</small>
        </p>
        <p className="login-sample-what">
          Оборотный кредит, <span className="nowrap">ООО «Северный хлеб»</span>
        </p>
        <dl className="login-sample-terms">
          <div>
            <dt>Ставка</dt>
            <dd>{pct(18.5)}</dd>
          </div>
          <div>
            <dt>Платёж в месяц</dt>
            <dd>{money(275_754)}</dd>
          </div>
        </dl>
        <div className="login-sample-why">
          <span>Помогло решению</span>
          <p>
            <CirclePlus size={16} aria-hidden /> Кредиты погашались без просрочек
          </p>
          <p>
            <CirclePlus size={16} aria-hidden /> Бизнес работает больше 5 лет
          </p>
        </div>
      </div>
      <figcaption className="sr-only">Пример решения банка по демо-заявке</figcaption>
    </figure>
  );
}

/** «25 млн ₽», «60 мес.» не разрываются переносом строки */
const keepUnits = (s: string) => s.replace(/(\d) (?=мес\.|млн|тыс\.)/g, "$1 ").replace(/(млн|тыс\.) ₽/g, "$1 ₽");

function AccountCard({ a, disabled, onPick }: { a: DemoAccount; disabled: boolean; onPick: (a: DemoAccount) => void }) {
  return (
    <button className="account" disabled={disabled} onClick={() => onPick(a)}>
      <span className="scen">{a.scenario_title ?? a.title}</span>
      <span className="what">{keepUnits(a.scenario_hint ?? a.scenario)}</span>
      <span className="who">
        {a.full_name}, {a.login}
      </span>
    </button>
  );
}

export function LoginPage() {
  const { me, login } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const toast = useToast();
  // после входа — на страницу, которую открывали по ссылке или где закончилась сессия (если она своей роли), иначе на главную
  const from = (loc.state as { from?: string } | null)?.from;
  const target = (role: string) => {
    const home = role === "CLIENT" ? "/c" : "/b";
    return from && (from === home || from.startsWith(`${home}/`)) ? from : home;
  };
  const [expired] = useState(sessionExpired);
  useEffect(() => {
    forgetSessionExpired();
    document.title = "Вход — Кредитный кабинет для бизнеса";
  }, []);
  const [form, setForm] = useState({ login: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const demo = useQuery({
    queryKey: ["demo-accounts"],
    queryFn: () => api<DemoAccount[]>("/api/v1/auth/demo-accounts"),
    retry: false,
  });

  // вход завершился, пока страница ещё открыта, — сюда же, куда ведёт и сама кнопка входа
  if (me) return <Navigate to={target(me.user.role)} replace />;

  const doLogin = async (l: string, p: string, oneClick = false) => {
    setBusy(true);
    setError(null);
    try {
      const who = await login(l, p);
      nav(target(who.user.role), { replace: true });
    } catch (e) {
      // ошибку входа в один клик показываем уведомлением: на телефоне форма с полем ошибки — ниже списка
      if (oneClick) toast.error(e);
      else setError(errorText(e));
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
        <div className="login-side-in">
          <div className="brand">
            <Brand title="Кредитный кабинет" subtitle="для малого бизнеса" />
          </div>
          <h1>Кредит для бизнеса&nbsp;— от задачи до денег на счёте</h1>
          <p>
            Весь путь — в одном кабинете. Банк рассматривает заявку сразу: автоматически, когда это безопасно, и со специалистом, когда
            нужна экспертиза.
          </p>
          <Showcase />
        </div>
      </aside>
      <main className="login-main">
        <div>
          <h2>Вход</h2>
          <p className="muted" style={{ marginTop: 6 }}>
            Войдите как предприниматель или как сотрудник банка.
          </p>
          {expired && (
            <div className="callout info" style={{ marginTop: 12 }}>
              Сессия закончилась — войдите снова, чтобы продолжить.
            </div>
          )}
        </div>
        <form className="stack login-form" onSubmit={onSubmit} style={{ maxWidth: 420 }}>
          <h3 className="login-form-title">Вход по логину и паролю</h3>
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
                <AccountCard key={a.login} a={a} disabled={busy} onPick={(x) => doLogin(x.login, x.password, true)} />
              ))}
            </div>
            <h3 style={{ marginTop: 8 }}>Сотрудники банка</h3>
            <div className="accounts">
              {staff.map((a) => (
                <AccountCard key={a.login} a={a} disabled={busy} onPick={(x) => doLogin(x.login, x.password, true)} />
              ))}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
