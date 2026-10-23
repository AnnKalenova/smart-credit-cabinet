import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { Dashboard } from "../../api/types";
import { Loader } from "../../components/ui";
import { useAuth } from "../../lib/auth";
import { dateTime, moneyShort } from "../../lib/format";
import { useToast } from "../../lib/toast";

const SERVICES = [
  { code: "EGRUL", label: "ЕГРЮЛ/ЕГРИП", short: "ЕГРЮЛ" },
  { code: "FNS", label: "ФНС «Прозрачный бизнес»", short: "ФНС" },
  { code: "BKI", label: "Бюро кредитных историй", short: "БКИ" },
  { code: "ABS", label: "АБС банка (выдача)", short: "АБС" },
];

function duration(minutes: number | null): string {
  if (minutes === null) return "—";
  if (minutes < 1) return `${Math.max(1, Math.round(minutes * 60))} с`;
  if (minutes < 120) return `${Math.round(minutes)} мин`;
  return `${(minutes / 60).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} ч`;
}

function FaultSimulator() {
  const toast = useToast();
  const qc = useQueryClient();
  const faults = useQuery({
    queryKey: ["faults"],
    queryFn: () => api<{ faults: Record<string, { mode: string; seconds_left: number }> }>("/api/v1/bank/integrations/faults"),
    refetchInterval: 3000,
  });
  const set = useMutation({
    mutationFn: (body: { service: string; mode: string }) =>
      api("/api/v1/bank/integrations/faults", { method: "POST", json: { ...body, duration_seconds: 120 } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["faults"] }),
    onError: (e) => toast.error(e),
  });
  return (
    <section className="panel">
      <h3>Проверка отказоустойчивости</h3>
      <p className="small muted" style={{ marginTop: 6 }}>
        Смоделируйте сбой внешней системы на 2 минуты и подайте заявку: конвейер повторит запрос с нарастающей паузой, а если сервис не
        вернётся — передаст заявку специалисту, а не «уронит» её.
      </p>
      <div className="stack-s" style={{ marginTop: 12 }}>
        {SERVICES.map((s) => {
          const f = faults.data?.faults[s.code];
          return (
            <div key={s.code} style={{ borderTop: "1px solid var(--rule)", paddingTop: 10 }}>
              <div className="row small">
                <b>{s.label}</b>
                <span className="spacer" />
                <span className={f ? "neg xs" : "faint xs"}>
                  {f ? (f.mode === "down" ? `недоступен ещё ${f.seconds_left} с` : `медленный ещё ${f.seconds_left} с`) : "работает штатно"}
                </span>
              </div>
              <div className="row" style={{ gap: 6, marginTop: 6 }}>
                {f ? (
                  <button className="btn btn-primary btn-s" onClick={() => set.mutate({ service: s.code, mode: "ok" })}>
                    Восстановить
                  </button>
                ) : (
                  <>
                    <button className="btn btn-ghost btn-s" onClick={() => set.mutate({ service: s.code, mode: "down" })}>
                      Отключить
                    </button>
                    <button className="btn btn-ghost btn-s" onClick={() => set.mutate({ service: s.code, mode: "slow" })}>
                      Замедлить
                    </button>
                  </>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

export function BankDashboard() {
  const { dict } = useAuth();
  const d = useQuery({ queryKey: ["dashboard"], queryFn: () => api<Dashboard>("/api/v1/bank/dashboard"), refetchInterval: 5000 });
  if (!d.data) return <Loader />;
  const x = d.data;
  const funnelMax = Math.max(1, ...x.funnel.map((f) => f.count));
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Мониторинг конвейера</h1>
          <p className="muted">Обновляется автоматически, {dateTime(x.generated_at)}</p>
        </div>
      </div>
      <div className="kpis">
        <div className="kpi">
          <div className="l">На ручном рассмотрении</div>
          <div className="v">{x.manual_queue}</div>
          <div className={`xs ${x.manual_overdue ? "sla-bad" : "faint"}`}>{x.manual_overdue ? `просрочено: ${x.manual_overdue}` : "просрочек нет"}</div>
        </div>
        <div className="kpi">
          <div className="l">Решено автоматически</div>
          <div className="v">{x.auto_decision_share === null ? "—" : `${Math.round(x.auto_decision_share * 100)}%`}</div>
          <div className="xs faint">из итоговых решений</div>
        </div>
        <div className="kpi">
          <div className="l">От подачи до решения</div>
          <div className="v">{duration(x.avg_minutes_to_decision)}</div>
          <div className="xs faint">в среднем</div>
        </div>
        <div className="kpi">
          <div className="l">Выдано кредитов</div>
          <div className="v">{moneyShort(x.issued_amount)}</div>
          <div className="xs faint">{x.avg_minutes_to_issue === null ? "" : `от подачи до денег в среднем ${duration(x.avg_minutes_to_issue)}`}</div>
        </div>
      </div>

      <div className="grid-2" style={{ marginTop: 20 }}>
        <div className="stack">
          <section className="panel">
            <h3>Воронка</h3>
            <div className="funnel" style={{ marginTop: 14 }}>
              {x.funnel.map((f) => (
                <div key={f.stage} className="funnel-row">
                  <span>{f.stage}</span>
                  <div className="funnel-bar" style={{ width: `${(f.count / funnelMax) * 100}%`, minWidth: 4 }} />
                  <b className="num">{f.count}</b>
                </div>
              ))}
              <div className="funnel-row">
                <span>Отказ банка</span>
                <div className="funnel-bar" style={{ width: `${(x.declined / funnelMax) * 100}%`, minWidth: 4, background: "var(--stop)" }} />
                <b className="num">{x.declined}</b>
              </div>
            </div>
          </section>
          <section className="panel">
            <h3>Заявки по статусам</h3>
            <table className="table" style={{ marginTop: 8 }}>
              <tbody>
                {x.by_status.map((s) => (
                  <tr key={s.status}>
                    <td>{s.label}</td>
                    <td className="r">
                      <b>{s.count}</b>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          <section className="panel">
            <h3>Последние события</h3>
            <ol className="timeline timeline-plain" style={{ marginTop: 12 }}>
              {x.recent_events.map((e) => (
                <li key={e.id}>
                  <time>
                    {dateTime(e.created_at)}, <Link to={`/b/applications/${e.application_id}`}>№ {e.application_number}</Link>
                  </time>
                  {e.title}
                </li>
              ))}
            </ol>
          </section>
        </div>
        <aside className="stack">
          <section className="panel">
            <h3>Внешние системы за час</h3>
            {x.integrations_last_hour.length === 0 && <p className="small muted">Вызовов не было</p>}
            {x.integrations_last_hour.length > 0 && (
              <table className="table" style={{ marginTop: 8 }}>
                <thead>
                  <tr>
                    <th>Сервис</th>
                    <th className="r">Вызовы</th>
                    <th className="r">Ошибки</th>
                    <th className="r">мс</th>
                  </tr>
                </thead>
                <tbody>
                  {x.integrations_last_hour.map((i) => (
                    <tr key={i.service}>
                      <td>{SERVICES.find((s) => s.code === i.service)?.short ?? i.service}</td>
                      <td className="r">{i.calls}</td>
                      <td className={`r ${i.errors ? "neg" : ""}`}>{i.errors}</td>
                      <td className="r">{i.avg_ms}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
          {dict?.demo_mode && <FaultSimulator />}
        </aside>
      </div>
    </>
  );
}
