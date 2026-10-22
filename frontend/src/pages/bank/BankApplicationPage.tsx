import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, FileText, Plus, RotateCcw, Send, Trash2, UserCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, download } from "../../api/client";
import type { BankApplication, BankDecision, Message } from "../../api/types";
import { Field, Grade, Loader, Timeline, TurnoverBars } from "../../components/ui";
import { useAuth } from "../../lib/auth";
import { date, dateTime, formatInputAmount, money, parseAmount, pct, relativeDue } from "../../lib/format";
import { useToast } from "../../lib/toast";

const TABS = ["Сводка", "Скоринг", "Источники", "Документы и переписка", "Журнал"] as const;

// ------------------------------------------------------------------ решение аналитика

type OfferDraft = { product_code: string; amount: string; term_months: number; rate: string; label: string };

function recommendedOffers(app: BankApplication): OfferDraft[] {
  const auto = app.decisions.find((d) => d.kind === "AUTO" && d.details.offers?.length);
  const offers = auto?.details.offers ?? [];
  if (offers.length) {
    return offers.map((o) => ({
      product_code: o.product_code,
      amount: formatInputAmount(o.amount),
      term_months: o.term_months,
      rate: String(o.rate).replace(".", ","),
      label: o.label,
    }));
  }
  const hint = app.pricing_hints.find((h) => h.product_code === app.product_code);
  return [
    {
      product_code: app.product_code,
      amount: formatInputAmount(app.amount),
      term_months: app.term_months,
      rate: String(hint?.default_rate ?? "").replace(".", ","),
      label: "",
    },
  ];
}

const DECLINE_REASONS = [
  "Высокая долговая нагрузка: платежи по кредитам превысят допустимую долю выручки",
  "Негативная кредитная история",
  "Существенная задолженность перед бюджетом",
  "Недостаточно подтверждённой выручки для запрошенной суммы",
  "Признаки недостоверности сведений о компании",
];

function DecisionForm({ app }: { app: BankApplication }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { dict } = useAuth();
  const [action, setAction] = useState<"APPROVE" | "REQUEST_INFO" | "DECLINE">("APPROVE");
  const [offers, setOffers] = useState<OfferDraft[]>(() => recommendedOffers(app));
  const [docs, setDocs] = useState<string[]>([]);
  const [customItem, setCustomItem] = useState("");
  const [question, setQuestion] = useState("");
  const [reason, setReason] = useState(DECLINE_REASONS[0]);
  const [message, setMessage] = useState("");
  const [comment, setComment] = useState("");

  const submit = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = { action, message_to_client: message || null, comment_internal: comment || null };
      if (action === "APPROVE") {
        body.offers = offers.map((o) => ({
          product_code: o.product_code,
          amount: parseAmount(o.amount),
          term_months: Number(o.term_months),
          rate: o.rate ? Number(o.rate.replace(",", ".")) : null,
          label: o.label || null,
        }));
      } else if (action === "REQUEST_INFO") {
        const items: { type: string; doc_type?: string; label: string }[] = docs.map((d) => ({ type: "DOCUMENT", doc_type: d, label: dict?.doc_types[d] ?? d }));
        if (customItem.trim()) items.push({ type: "DOCUMENT", doc_type: "OTHER", label: customItem.trim() });
        if (question.trim()) items.push({ type: "COMMENT", label: question.trim() });
        body.info_items = items;
      } else {
        body.reason = reason;
      }
      return api(`/api/v1/bank/applications/${app.id}/decision`, { method: "POST", json: body });
    },
    onSuccess: () => {
      toast.ok(action === "APPROVE" ? "Предложение отправлено клиенту" : action === "REQUEST_INFO" ? "Запрос отправлен клиенту" : "Отказ оформлен");
      void qc.invalidateQueries({ queryKey: ["bank-app", app.id] });
      void qc.invalidateQueries({ queryKey: ["bank-apps"] });
    },
    onError: (e) => toast.error(e),
  });

  const updateOffer = (i: number, patch: Partial<OfferDraft>) => setOffers(offers.map((o, j) => (i === j ? { ...o, ...patch } : o)));

  return (
    <section className="panel" style={{ borderColor: "var(--ink)" }}>
      <h2>Решение</h2>
      <div className="segmented" style={{ marginTop: 12 }} role="tablist">
        {(
          [
            ["APPROVE", "Одобрить"],
            ["REQUEST_INFO", "Запросить"],
            ["DECLINE", "Отказать"],
          ] as const
        ).map(([k, l]) => (
          <button key={k} className={action === k ? "on" : ""} onClick={() => setAction(k)} role="tab" aria-selected={action === k}>
            {l}
          </button>
        ))}
      </div>
      <div className="stack" style={{ marginTop: 16 }}>
        {action === "APPROVE" && (
          <>
            <p className="small muted">Условия подставлены из рекомендации системы — их можно изменить в пределах продукта.</p>
            {offers.map((o, i) => {
              const hint = app.pricing_hints.find((h) => h.product_code === o.product_code);
              return (
                <div key={i} className="panel-flat stack-s" style={{ padding: 12 }}>
                  <div className="row">
                    <b className="small">Вариант {i + 1}</b>
                    <span className="spacer" />
                    {offers.length > 1 && (
                      <button className="icon-btn" style={{ width: 28, height: 28, color: "var(--ink-3)" }} onClick={() => setOffers(offers.filter((_, j) => j !== i))} aria-label="Убрать вариант">
                        <Trash2 size={15} />
                      </button>
                    )}
                  </div>
                  <select
                    className="select"
                    value={o.product_code}
                    onChange={(e) => {
                      const h = app.pricing_hints.find((x) => x.product_code === e.target.value);
                      updateOffer(i, { product_code: e.target.value, rate: String(h?.default_rate ?? "").replace(".", ",") });
                    }}
                  >
                    {app.pricing_hints.map((h) => (
                      <option key={h.product_code} value={h.product_code}>
                        {h.product_name}
                      </option>
                    ))}
                  </select>
                  <Field label="Сумма, ₽">
                    <input className="input input-money" maxLength={17} value={o.amount} onChange={(e) => updateOffer(i, { amount: formatInputAmount(e.target.value) })} />
                  </Field>
                  <div className="row">
                    <Field label="Срок, мес.">
                      <input className="input" type="number" value={o.term_months} onChange={(e) => updateOffer(i, { term_months: Number(e.target.value) })} />
                    </Field>
                    <Field label="Ставка, %">
                      <input className="input" inputMode="decimal" maxLength={6} value={o.rate} onChange={(e) => updateOffer(i, { rate: e.target.value })} />
                    </Field>
                  </div>
                  {hint && (
                    <span className="xs faint">
                      {money(hint.min_amount)}–{money(hint.max_amount)}, {hint.min_term}–{hint.max_term} мес., ставка {pct(hint.min_rate)}–{pct(hint.max_rate)}, по политике{" "}
                      {pct(hint.default_rate)}
                    </span>
                  )}
                </div>
              );
            })}
            {offers.length < 3 && (
              <button className="btn btn-ghost btn-s" onClick={() => setOffers([...offers, { ...offers[offers.length - 1], label: "" }])}>
                <Plus size={15} /> Добавить вариант
              </button>
            )}
          </>
        )}
        {action === "REQUEST_INFO" && (
          <>
            <span className="small muted">Какие документы нужны</span>
            <div className="stack-s">
              {Object.entries(dict?.doc_types ?? {})
                .filter(([k]) => !["OTHER", "EXPLANATION"].includes(k))
                .map(([k, label]) => (
                  <label key={k} className="check">
                    <input type="checkbox" checked={docs.includes(k)} onChange={(e) => setDocs(e.target.checked ? [...docs, k] : docs.filter((d) => d !== k))} />
                    {label}
                  </label>
                ))}
            </div>
            <Field label="Другой документ">
              <input className="input" maxLength={300} value={customItem} placeholder="Например, справка об отсутствии налогового долга" onChange={(e) => setCustomItem(e.target.value)} />
            </Field>
            <Field label="Вопрос клиенту">
              <input className="input" maxLength={300} value={question} placeholder="Например, расскажите о причинах смены руководителя" onChange={(e) => setQuestion(e.target.value)} />
            </Field>
          </>
        )}
        {action === "DECLINE" && (
          <Field label="Причина отказа">
            <select className="select" value={reason} onChange={(e) => setReason(e.target.value)}>
              {DECLINE_REASONS.map((r) => (
                <option key={r}>{r}</option>
              ))}
            </select>
          </Field>
        )}
        <Field label="Сообщение клиенту" hint="Клиент увидит его в заявке и в переписке">
          <textarea className="textarea" style={{ minHeight: 70 }} maxLength={4000} value={message} onChange={(e) => setMessage(e.target.value)} />
        </Field>
        <Field label="Комментарий для банка" hint="Виден только сотрудникам, сохраняется в журнале решения">
          <textarea className="textarea" style={{ minHeight: 60 }} maxLength={4000} value={comment} onChange={(e) => setComment(e.target.value)} />
        </Field>
        <button
          className={`btn ${action === "DECLINE" ? "btn-danger" : "btn-primary"}`}
          disabled={submit.isPending || (action === "REQUEST_INFO" && !docs.length && !customItem.trim() && !question.trim())}
          onClick={() => submit.mutate()}
        >
          <Send size={17} /> {action === "APPROVE" ? "Отправить предложение" : action === "REQUEST_INFO" ? "Отправить запрос" : "Оформить отказ"}
        </button>
      </div>
    </section>
  );
}

function DecisionCard({ d }: { d: BankDecision }) {
  return (
    <div className="panel-flat" style={{ padding: "12px 14px" }}>
      <div className="row">
        <b>{d.outcome_label}</b>
        <span className="spacer" />
        <span className="xs faint">
          {d.kind === "AUTO" ? "система" : d.decided_by}, {dateTime(d.created_at)}
        </span>
      </div>
      <ul className="small" style={{ margin: "6px 0 0", paddingLeft: 18 }}>
        {d.reasons
          .filter((r) => r.kind !== "POSITIVE")
          .map((r, i) => (
            <li key={i}>{r.text}</li>
          ))}
      </ul>
      {d.details.recommendation && (
        <p className="small" style={{ marginTop: 6 }}>
          Рекомендация системы: <b>{d.details.recommendation === "APPROVE" ? "одобрить запрошенные условия" : "встречное предложение"}</b>
          {d.details.offers?.length ? `: ${d.details.offers.map((o) => `${money(o.amount)} на ${o.term_months} мес. под ${pct(o.rate)}`).join("; ")}` : ""}
        </p>
      )}
      {d.comment_internal && <p className="small muted" style={{ marginTop: 6 }}>Комментарий: {d.comment_internal}</p>}
    </div>
  );
}

// ------------------------------------------------------------------ вкладки досье

function Summary({ app }: { app: BankApplication }) {
  const { dict } = useAuth();
  const lastDecision = app.decisions[0];
  return (
    <div className="stack">
      <section className="panel">
        <h3>Запрос клиента</h3>
        <dl className="kv">
          <dt>Продукт</dt>
          <dd>{app.product_name}</dd>
          <dt>Сумма и срок</dt>
          <dd>
            {money(app.amount)} на {app.term_months} мес.
          </dd>
          <dt>Цель</dt>
          <dd>
            {dict?.purposes.find((p) => p.code === app.purpose)?.label}
            {app.purpose_details ? `: ${app.purpose_details}` : ""}
          </dd>
          <dt>Выручка со слов клиента</dt>
          <dd>{money(app.declared_revenue)} в год</dd>
          <dt>Обеспечение</dt>
          <dd>
            {app.collateral_description || (app.has_collateral ? "залог" : "без залога")}
            {app.owner_guarantee ? "; поручительство собственника" : ""}
          </dd>
          <dt>Контакт</dt>
          <dd>
            {app.contact_name}, {app.contact_phone}
          </dd>
          <dt>Заявку подготовил</dt>
          <dd>{app.created_by}</dd>
        </dl>
      </section>
      {lastDecision && (
        <section className="panel">
          <h3>Решения по заявке</h3>
          <div className="stack-s" style={{ marginTop: 12 }}>
            {app.decisions.map((d) => (
              <DecisionCard key={d.id} d={d} />
            ))}
          </div>
        </section>
      )}
      {app.checks.length > 0 && (
        <section className="panel">
          <h3>Проверка полноты и корректности</h3>
          <div className="stack-s" style={{ marginTop: 12 }}>
            {app.checks.map((c) => (
              <div key={c.code} className="row small">
                {c.severity === "OK" ? <CircleCheck size={16} className="pos" /> : <CircleAlert size={16} className="neg" />}
                {c.message}
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function Scoring({ app }: { app: BankApplication }) {
  const s = app.scoring;
  const calc = app.decisions.find((d) => d.details.limit_calculation)?.details;
  if (!s) return <div className="panel muted">Скоринг ещё не рассчитан.</div>;
  const maxAbs = Math.max(...s.factors.map((f) => Math.abs(f.points)), 1);
  const m = s.metrics;
  return (
    <div className="stack">
      <section className="panel">
        <div className="score-dial">
          <span className="num">{s.score}</span>
          <Grade grade={s.grade} />
          <div>
            <b>{s.grade_label}</b>
            <div className="small muted">
              Условная вероятность дефолта {pct(s.pd * 100)}, модель {s.model_version}, базовый балл 600
            </div>
          </div>
        </div>
        <div style={{ marginTop: 16 }}>
          {s.factors.map((f) => (
            <div key={f.code} className="factor-row">
              <div>
                <b>{f.name}</b>
                <div className="xs muted">{f.comment}</div>
              </div>
              <div className="factor-bar" aria-hidden>
                <span
                  style={{
                    left: f.points >= 0 ? "50%" : `${50 - (Math.abs(f.points) / maxAbs) * 50}%`,
                    width: `${(Math.abs(f.points) / maxAbs) * 50}%`,
                    background: f.points >= 0 ? "var(--go)" : "var(--stop)",
                  }}
                />
              </div>
              <span className={`num ${f.points > 0 ? "pos" : f.points < 0 ? "neg" : "faint"}`}>
                {f.points > 0 ? "+" : ""}
                {f.points}
              </span>
              <span className="val xs muted">{f.value}</span>
            </div>
          ))}
        </div>
      </section>
      <section className="panel">
        <h3>Ключевые показатели</h3>
        <dl className="kv">
          <dt>Поступления в месяц</dt>
          <dd>
            {money(m.avg_monthly_turnover)}
            {m.effective_monthly_turnover !== m.avg_monthly_turnover ? ` (с учётом выписки: ${money(m.effective_monthly_turnover)})` : ""}
          </dd>
          <dt>Платежи по текущим кредитам</dt>
          <dd>{money(m.existing_monthly_payments)} в месяц</dd>
          <dt>Платёж по новому кредиту</dt>
          <dd>{money(m.new_monthly_payment)} в месяц (по ориентировочной ставке)</dd>
          <dt>Долговая нагрузка</dt>
          <dd>{pct(m.dti * 100)} поступлений</dd>
          <dt>Балл БКИ</dt>
          <dd>{m.bki_score ?? "нет истории"}</dd>
        </dl>
      </section>
      {calc?.limit_calculation && (
        <section className="panel">
          <h3>Расчёт лимита и ставки</h3>
          <dl className="kv">
            <dt>По оборотам</dt>
            <dd>
              {money(calc.limit_calculation.by_turnover)} (×{calc.limit_calculation.turnover_multiplier} месячных поступлений)
            </dd>
            <dt>По платёжеспособности</dt>
            <dd>
              {money(calc.limit_calculation.by_payment_capacity)} (свободно {money(calc.limit_calculation.payment_capacity)} в месяц)
            </dd>
            <dt>Максимум продукта</dt>
            <dd>{money(calc.limit_calculation.product_max)}</dd>
            <dt>Коэффициент рейтинга</dt>
            <dd>×{calc.limit_calculation.grade_factor}</dd>
            <dt>Итоговый лимит</dt>
            <dd>
              <b>{money(calc.limit_calculation.max_limit)}</b> на {calc.limit_calculation.term_months} мес.
            </dd>
          </dl>
          {calc.pricing && (
            <table className="table" style={{ marginTop: 12 }}>
              <tbody>
                {calc.pricing.map((p) => (
                  <tr key={p.label}>
                    <td>{p.label}</td>
                    <td className="r">{pct(p.value, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}
    </div>
  );
}

function Sources({ app }: { app: BankApplication }) {
  const [raw, setRaw] = useState<string | null>(null);
  const eg = app.sources.EGRUL?.payload;
  const fns = app.sources.FNS?.payload;
  const bki = app.sources.BKI?.payload;
  const internal = app.sources.INTERNAL?.payload;
  const yes = (v: boolean) => (v ? <b className="neg">да</b> : "нет");
  return (
    <div className="stack">
      {internal && (
        <section className="panel">
          <div className="panel-head">
            <h3>Обороты по счёту в банке</h3>
            <span className="small muted">в среднем {money(internal.avg_monthly_turnover)} в месяц</span>
          </div>
          <TurnoverBars series={internal.series} />
        </section>
      )}
      {eg && (
        <section className="panel">
          <div className="panel-head">
            <h3>ЕГРЮЛ / ЕГРИП</h3>
            <button className="btn-link xs" onClick={() => setRaw(JSON.stringify(eg, null, 2))}>
              Ответ сервиса
            </button>
          </div>
          <dl className="kv">
            <dt>Статус</dt>
            <dd className={eg.status !== "ACTIVE" ? "neg" : ""}>{eg.status_text}</dd>
            <dt>Регистрация</dt>
            <dd>{date(eg.registration_date)}</dd>
            <dt>Руководитель</dt>
            <dd>
              {eg.director?.name}, {eg.director?.position}, с {date(eg.director?.since)}
            </dd>
            {eg.previous_director && (
              <>
                <dt>Прежний руководитель</dt>
                <dd>
                  {eg.previous_director.name}, до {date(eg.previous_director.until)}
                </dd>
              </>
            )}
            {eg.founders?.length > 0 && (
              <>
                <dt>Учредители</dt>
                <dd>{eg.founders.map((f: { name: string; share: number }) => `${f.name} (${f.share}%)`).join(", ")}</dd>
              </>
            )}
            <dt>ОКВЭД</dt>
            <dd>
              {eg.okved_main?.code}, {eg.okved_main?.name}
            </dd>
          </dl>
        </section>
      )}
      {fns && (
        <section className="panel">
          <div className="panel-head">
            <h3>ФНС «Прозрачный бизнес»</h3>
            <button className="btn-link xs" onClick={() => setRaw(JSON.stringify(fns, null, 2))}>
              Ответ сервиса
            </button>
          </div>
          <dl className="kv">
            <dt>Режим налогообложения</dt>
            <dd>{fns.tax_regime}</dd>
            <dt>Выручка за {fns.reporting_year}</dt>
            <dd>{money(fns.revenue_last_year)}</dd>
            <dt>Чистая прибыль</dt>
            <dd>{fns.net_profit_last_year === null ? "нет публичной отчётности" : money(fns.net_profit_last_year)}</dd>
            <dt>Задолженность по налогам</dt>
            <dd className={fns.tax_arrears > 0 ? "neg" : ""}>{money(fns.tax_arrears)}</dd>
            <dt>Блокировки счетов</dt>
            <dd>{yes(fns.suspended_accounts)}</dd>
            <dt>Массовый адрес</dt>
            <dd>{yes(fns.mass_address)}</dd>
            <dt>Недостоверные сведения</dt>
            <dd>{yes(fns.unreliable_data)}</dd>
            <dt>Среднесписочная численность</dt>
            <dd>{fns.avg_headcount}</dd>
          </dl>
        </section>
      )}
      {bki && (
        <section className="panel">
          <div className="panel-head">
            <h3>Кредитная история (БКИ)</h3>
            <button className="btn-link xs" onClick={() => setRaw(JSON.stringify(bki, null, 2))}>
              Ответ сервиса
            </button>
          </div>
          <dl className="kv">
            <dt>Балл БКИ</dt>
            <dd>{bki.score ?? "—"}</dd>
            <dt>Длина истории</dt>
            <dd>{bki.history_length_months} мес.</dd>
            <dt>Макс. просрочка за 12 мес.</dt>
            <dd className={bki.max_overdue_days_12m > 0 ? "neg" : ""}>{bki.max_overdue_days_12m} дн.</dd>
            <dt>Запросов за 30 дней</dt>
            <dd>{bki.inquiries_30d}</dd>
            <dt>Платежи по кредитам</dt>
            <dd>{money(bki.total_monthly_payment)} в месяц</dd>
          </dl>
          {bki.loans?.length > 0 && (
            <div className="table-wrap" style={{ marginTop: 12 }}>
              <table className="table">
                <thead>
                  <tr>
                    <th>Кредитор</th>
                    <th>Вид</th>
                    <th className="r">Остаток</th>
                    <th className="r">Платёж</th>
                    <th className="r">Просрочка</th>
                  </tr>
                </thead>
                <tbody>
                  {bki.loans.map((l: Record<string, any>, i: number) => (
                    <tr key={i}>
                      <td>{l.creditor}</td>
                      <td>{l.type}</td>
                      <td className="r nowrap">{money(l.outstanding)}</td>
                      <td className="r nowrap">{money(l.monthly_payment)}</td>
                      <td className={`r ${l.max_overdue_days_12m ? "neg" : ""}`}>{l.max_overdue_days_12m} дн.</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
      {!eg && !fns && !bki && <div className="panel muted">Сведения из внешних источников ещё не получены.</div>}
      {raw && (
        <section className="panel">
          <div className="panel-head">
            <h3>Ответ сервиса</h3>
            <button className="btn-link xs" onClick={() => setRaw(null)}>
              Скрыть
            </button>
          </div>
          <pre className="json">{raw}</pre>
        </section>
      )}
    </div>
  );
}

function DocsAndChat({ app }: { app: BankApplication }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [text, setText] = useState("");
  const msgs = useQuery({
    queryKey: ["bank-messages", app.id],
    queryFn: () => api<Message[]>(`/api/v1/bank/applications/${app.id}/messages`),
    refetchInterval: 6000,
  });
  const send = useMutation({
    mutationFn: () => api(`/api/v1/bank/applications/${app.id}/messages`, { method: "POST", json: { text } }),
    onSuccess: () => {
      setText("");
      void qc.invalidateQueries({ queryKey: ["bank-messages", app.id] });
    },
    onError: (e) => toast.error(e),
  });
  return (
    <div className="stack">
      <section className="panel">
        <h3>Документы клиента</h3>
        {app.documents.length === 0 && <p className="small muted" style={{ marginTop: 8 }}>Документы не прикладывались.</p>}
        <div className="stack-s" style={{ marginTop: 10 }}>
          {app.documents.map((d) => (
            <div key={d.id} className="row small">
              <FileText size={16} className="faint" />
              <button className="btn-link" onClick={() => download(`/api/v1/bank/applications/${app.id}/documents/${d.id}`, d.file_name)}>
                {d.file_name}
              </button>
              <span className="faint">
                {d.doc_type_label}, {dateTime(d.uploaded_at)}
              </span>
            </div>
          ))}
        </div>
        {app.consents.length > 0 && (
          <p className="xs faint" style={{ marginTop: 12 }}>
            Согласия на запрос в БКИ и обработку данных подписаны: {app.consents[0].signed_by}, {dateTime(app.consents[0].signed_at)}
          </p>
        )}
      </section>
      <section className="panel">
        <h3>Переписка с клиентом</h3>
        <div className="chat" style={{ marginTop: 12 }}>
          {(msgs.data ?? []).length === 0 && <p className="small faint">Сообщений пока нет.</p>}
          {msgs.data?.map((m) => (
            <div key={m.id} className={`bubble ${m.author_role !== "CLIENT" ? "mine" : ""}`}>
              <b className="xs">{m.author_name}</b>
              <div>{m.text}</div>
              <time>{dateTime(m.created_at)}</time>
            </div>
          ))}
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <input className="input" placeholder="Сообщение клиенту" maxLength={4000} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && text.trim() && send.mutate()} />
          <button className="btn btn-primary" disabled={!text.trim()} onClick={() => send.mutate()} aria-label="Отправить">
            <Send size={17} />
          </button>
        </div>
      </section>
    </div>
  );
}

function Journal({ app }: { app: BankApplication }) {
  return (
    <div className="stack">
      <section className="panel">
        <h3>Журнал событий</h3>
        <p className="xs faint" style={{ marginTop: 4 }}>Серым — внутренние события, которые клиент не видит</p>
        <div style={{ marginTop: 12 }}>
          <Timeline events={app.events} showInternal />
        </div>
      </section>
      <section className="panel">
        <h3>Вызовы внешних систем</h3>
        <div className="table-wrap" style={{ marginTop: 8 }}>
          <table className="table">
            <thead>
              <tr>
                <th>Время</th>
                <th>Сервис</th>
                <th>Результат</th>
                <th className="r">мс</th>
                <th className="r">Попытка</th>
              </tr>
            </thead>
            <tbody>
              {app.integration_calls.map((c, i) => (
                <tr key={i}>
                  <td className="nowrap">{dateTime(c.created_at)}</td>
                  <td>
                    {c.service}
                    <div className="xs faint">{c.operation}</div>
                  </td>
                  <td className={c.status === "SUCCESS" ? "pos" : "neg"}>
                    {c.status}
                    {c.error ? <div className="xs faint">{c.error}</div> : null}
                  </td>
                  <td className="r">{c.duration_ms}</td>
                  <td className="r">{c.attempt}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

// ------------------------------------------------------------------ страница

export function BankApplicationPage() {
  const { id } = useParams<{ id: string }>();
  const { me } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useState<(typeof TABS)[number]>("Сводка");
  const q = useQuery({
    queryKey: ["bank-app", id],
    queryFn: () => api<BankApplication>(`/api/v1/bank/applications/${id}`),
    refetchInterval: 3000,
  });
  const decisionKey = useMemo(() => q.data?.decisions.map((d) => d.id).join(",") ?? "", [q.data]);
  const [formKey, setFormKey] = useState(0);
  useEffect(() => setFormKey((k) => k + 1), [decisionKey]);

  const act = async (path: string, ok: string) => {
    try {
      await api(`/api/v1/bank/applications/${id}/${path}`, { method: "POST" });
      toast.ok(ok);
      void qc.invalidateQueries({ queryKey: ["bank-app", id] });
    } catch (e) {
      toast.error(e);
    }
  };

  if (q.isLoading) return <Loader />;
  if (!q.data) return <div className="panel">Заявка не найдена</div>;
  const app = q.data;
  const due = relativeDue(app.review_due_at);
  const mine = app.assignee === me?.user.full_name;

  return (
    <>
      <div className="crumbs">
        <Link to="/b">Заявки</Link>
        <span>/</span>
        <span>№ {app.number}</span>
      </div>
      <div className="page-head">
        <div>
          <h1>{app.company_name}</h1>
          <p className="muted">
            Заявка № {app.number}: {app.product_name}, {money(app.amount)} на {app.term_months} мес. ИНН {app.company_inn}
          </p>
        </div>
        <div className="row-wrap">
          {app.permissions.can_rerun && app.decisions[0]?.reasons.some((r) => r.code === "integration_unavailable") && (
            <button className="btn btn-ghost" onClick={() => act("rerun", "Автоматическая проверка запущена повторно")}>
              <RotateCcw size={17} /> Повторить проверку
            </button>
          )}
          {!mine && app.status === "MANUAL_REVIEW" && (
            <button className="btn btn-primary" onClick={() => act("assign", "Заявка закреплена за вами")}>
              <UserCheck size={17} /> Взять в работу
            </button>
          )}
        </div>
      </div>

      <div className="kpis" style={{ marginBottom: 20 }}>
        <div className="kpi">
          <div className="l">Статус</div>
          <div style={{ marginTop: 6 }}>
            <span className={`chip ${app.status === "MANUAL_REVIEW" ? "action" : app.status === "ISSUED" ? "positive" : app.status === "DECLINED" ? "negative" : "progress"}`}>{app.status_label}</span>
          </div>
          {app.stage_detail && <div className="xs faint" style={{ marginTop: 4 }}>{app.stage_detail}</div>}
        </div>
        <div className="kpi">
          <div className="l">Скоринг</div>
          <div className="row" style={{ marginTop: 4 }}>
            <Grade grade={app.grade} />
            <span className="num" style={{ fontSize: 20 }}>{app.score ?? "—"}</span>
          </div>
        </div>
        <div className="kpi">
          <div className="l">Срок решения</div>
          <div className={`v ${due.overdue ? "sla-bad" : ""}`} style={{ fontSize: 16 }}>
            {app.status === "MANUAL_REVIEW" ? due.text : "—"}
          </div>
        </div>
        <div className="kpi">
          <div className="l">Специалист</div>
          <div style={{ marginTop: 6 }}>{app.assignee ?? <span className="faint">не назначен</span>}</div>
        </div>
      </div>

      <div className="grid-2">
        <div>
          <div className="tabs" role="tablist">
            {TABS.map((t) => (
              <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
                {t}
              </button>
            ))}
          </div>
          {tab === "Сводка" && <Summary app={app} />}
          {tab === "Скоринг" && <Scoring app={app} />}
          {tab === "Источники" && <Sources app={app} />}
          {tab === "Документы и переписка" && <DocsAndChat app={app} />}
          {tab === "Журнал" && <Journal app={app} />}
        </div>
        <aside className="stack">
          {app.permissions.can_decide ? (
            <>
              {app.triggers.length > 0 && (
                <section className="panel">
                  <h3>Почему заявка у специалиста</h3>
                  <ul className="small" style={{ margin: "10px 0 0", paddingLeft: 18 }}>
                    {app.triggers.map((t) => (
                      <li key={t}>{t}</li>
                    ))}
                  </ul>
                </section>
              )}
              <DecisionForm key={formKey} app={app} />
            </>
          ) : (
            <section className="panel">
              <h3>Решение</h3>
              <p className="small muted" style={{ marginTop: 8 }}>
                {app.status === "NEEDS_INFO"
                  ? "Ждём ответ клиента на запрос. После ответа заявка снова пройдёт автоматическую проверку."
                  : ["OFFER_READY", "SIGNING", "DISBURSEMENT", "ISSUED"].includes(app.status)
                    ? "Решение принято, дальше клиент работает с предложением."
                    : app.status === "DECLINED"
                      ? "По заявке оформлен отказ."
                      : "Заявка обрабатывается конвейером автоматически."}
              </p>
              {app.offers.filter((o) => ["ACTIVE", "ACCEPTED"].includes(o.status)).map((o) => (
                <p key={o.id} className="small" style={{ marginTop: 8 }}>
                  {o.status === "ACCEPTED" ? "Принято клиентом" : "Предложено"}: {money(o.amount)} на {o.term_months} мес. под {pct(o.rate)}
                </p>
              ))}
              {app.loan && (
                <p className="small" style={{ marginTop: 8 }}>
                  Выдан {date(app.loan.issued_at)}, договор {app.loan.contract_number}, счёт {app.loan.loan_account}
                </p>
              )}
            </section>
          )}
          <section className="panel">
            <h3>Клиент</h3>
            <dl className="kv">
              <dt>Клиент банка с</dt>
              <dd>{date(app.company.client_since)}</dd>
              <dt>Сегмент</dt>
              <dd>{app.company.segment}</dd>
              <dt>Сотрудников</dt>
              <dd>{app.company.headcount ?? "—"}</dd>
              <dt>Право подписи</dt>
              <dd>
                {app.company.members
                  .filter((m) => m.can_sign)
                  .map((m) => m.full_name)
                  .join(", ")}
              </dd>
            </dl>
          </section>
        </aside>
      </div>
    </>
  );
}
