import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import type { ApplicationSummary, CompanyProfile, Loan } from "../../api/types";
import { Empty, Loader, MiniTrack, PROCESSING, StatusChip, TurnoverBars } from "../../components/ui";
import { useAuth } from "../../lib/auth";
import { date, dateTime, money, moneyShort, pct } from "../../lib/format";

function greeting(fullName: string): string {
  const parts = fullName.split(" ");
  return parts.length >= 3 ? `${parts[1]} ${parts[2]}` : fullName;
}

export function ClientHome() {
  const { me, company, dict } = useAuth();
  const nav = useNavigate();
  const profile = useQuery({
    queryKey: ["company", company?.id],
    queryFn: () => api<CompanyProfile>(`/api/v1/companies/${company!.id}`),
    enabled: Boolean(company),
  });
  const apps = useQuery({
    queryKey: ["applications", company?.id],
    queryFn: () => api<ApplicationSummary[]>(`/api/v1/applications?company_id=${company!.id}`),
    enabled: Boolean(company),
    refetchInterval: (q) => ((q.state.data ?? []).some((a) => PROCESSING.has(a.status)) ? 2500 : 15000),
  });
  const loans = useQuery({
    queryKey: ["loans", company?.id],
    queryFn: () => api<Loan[]>(`/api/v1/loans?company_id=${company!.id}`),
    enabled: Boolean(company),
  });

  if (!me || !company) return <Loader />;
  const p = profile.data;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Здравствуйте, {greeting(me.user.full_name)}</h1>
          <p className="muted">
            {company.short_name}
            {p ? `, клиент банка с ${new Date(p.client_since).getFullYear()} года` : ""}
          </p>
        </div>
        <Link to="/c/match" className="btn btn-primary">
          <Plus size={18} /> Подобрать кредит
        </Link>
      </div>

      <div className="grid-2">
        <div className="stack">
          <section className="panel">
            <div className="panel-head">
              <h2>Заявки</h2>
            </div>
            {apps.isLoading && <Loader />}
            {apps.data && apps.data.length === 0 && (
              <Empty title="Заявок пока нет">
                <p className="small">Расскажите, на что нужны деньги, — подберём продукт и заполним заявку данными компании.</p>
                <Link to="/c/match" className="btn btn-signal" style={{ marginTop: 14 }}>
                  Подобрать кредит
                </Link>
              </Empty>
            )}
            {apps.data?.map((a) => (
              <Link key={a.id} to={`/c/applications/${a.id}`} className="list-app">
                <div>
                  <div className="title">
                    {a.product_name}, {money(a.amount)} на {a.term_months} мес.
                  </div>
                  <div className="small muted">
                    № {a.number}, {a.submitted_at ? `отправлена ${dateTime(a.submitted_at)}` : `создана ${dateTime(a.created_at)}`}
                  </div>
                  {a.stage_detail && PROCESSING.has(a.status) && <div className="small" style={{ marginTop: 4 }}>{a.stage_detail}</div>}
                </div>
                <div className="stack-s" style={{ alignItems: "flex-end" }}>
                  <StatusChip view={a.status_view} />
                  <MiniTrack status={a.status} view={a.status_view} />
                </div>
              </Link>
            ))}
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>Кредиты</h2>
            </div>
            {loans.data && loans.data.length === 0 && <p className="muted small">Выданных кредитов пока нет.</p>}
            {loans.data && loans.data.length > 0 && (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Договор</th>
                      <th>Продукт</th>
                      <th className="r">Сумма</th>
                      <th className="r">Ставка</th>
                      <th className="r">Платёж в месяц</th>
                      <th>Первый платёж</th>
                    </tr>
                  </thead>
                  <tbody>
                    {loans.data.map((l) => (
                      <tr key={l.id} className="clickable" onClick={() => nav(`/c/loans/${l.id}`)}>
                        <td>
                          <Link to={`/c/loans/${l.id}`}>{l.contract_number}</Link>
                        </td>
                        <td>{l.product_name}</td>
                        <td className="r nowrap">{money(l.amount)}</td>
                        <td className="r">{pct(l.rate)}</td>
                        <td className="r nowrap">{l.repayment_type === "REVOLVING" ? "проценты за использование" : money(l.monthly_payment)}</td>
                        <td>{date(l.first_payment_date)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>

        <aside className="stack">
          {p?.preapproved && (
            <section className="hero-limit">
              <div className="small" style={{ color: "rgba(255,255,255,.7)" }}>
                {p.preapproved.product_name}: предварительно доступно
              </div>
              <div className="big">{moneyShort(p.preapproved.amount)}</div>
              <p>
                Ставка от {pct(p.preapproved.rate_from)} годовых, срок до {p.preapproved.max_term} мес. {p.preapproved.note}
              </p>
              <button
                className="btn btn-signal"
                onClick={() => nav(`/c/match?purpose=OTHER&amount=${p.preapproved!.amount}&term=12`)}
              >
                Посмотреть условия
              </button>
            </section>
          )}

          <section className="panel">
            <h3>Поступления на счёт</h3>
            {p && (
              <p className="small muted" style={{ margin: "4px 0 14px" }}>
                В среднем {moneyShort(p.turnover.avg_monthly)} в месяц
              </p>
            )}
            {p ? <TurnoverBars series={p.turnover.series} /> : <Loader />}
            <p className="xs faint" style={{ marginTop: 10 }}>
              Банк учитывает обороты по вашему счёту — поэтому справки о доходах для экспресс-кредита не нужны.
            </p>
          </section>

          {p && (
            <section className="panel">
              <h3>Компания</h3>
              <dl className="kv">
                <dt>ИНН</dt>
                <dd>{p.inn}</dd>
                <dt>{p.kind === "IP" ? "ОГРНИП" : "ОГРН"}</dt>
                <dd>{p.ogrn}</dd>
                <dt>Вид деятельности</dt>
                <dd>
                  {p.okved_code}, {p.okved_name}
                </dd>
                <dt>Расчётный счёт</dt>
                <dd>{p.settlement_account}</dd>
                <dt>Право подписи</dt>
                <dd>
                  {p.members
                    .filter((m) => m.can_sign)
                    .map((m) => m.full_name)
                    .join(", ")}
                </dd>
              </dl>
            </section>
          )}
          {dict && (
            <p className="xs faint">
              Ключевая ставка Банка России: {pct(dict.key_rate, 2)}. {dict.key_rate_note}.
            </p>
          )}
        </aside>
      </div>
    </>
  );
}
