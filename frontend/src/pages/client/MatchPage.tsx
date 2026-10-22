import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../../api/client";
import type { Application, MatchResult } from "../../api/types";
import { Loader } from "../../components/ui";
import { useAuth } from "../../lib/auth";
import { formatInputAmount, money, moneyShort, parseAmount, pct } from "../../lib/format";
import { useToast } from "../../lib/toast";

const TERMS = [3, 6, 9, 12, 18, 24, 36, 48, 60, 84];
const PRESETS = [500_000, 1_000_000, 3_000_000, 5_000_000, 10_000_000, 25_000_000];

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function MatchPage() {
  const { company, dict } = useAuth();
  const nav = useNavigate();
  const toast = useToast();
  const [params] = useSearchParams();
  const [purpose, setPurpose] = useState(params.get("purpose") ?? "");
  const [amountText, setAmountText] = useState(params.get("amount") ? formatInputAmount(params.get("amount")!) : "");
  const [term, setTerm] = useState(Number(params.get("term") ?? 12));
  const amount = parseAmount(amountText);
  const query = useDebounced({ purpose, amount, term }, 350);

  const results = useQuery({
    queryKey: ["match", company?.id, query],
    queryFn: () =>
      api<{ key_rate: number; results: MatchResult[] }>("/api/v1/products/match", {
        method: "POST",
        json: { company_id: company!.id, purpose: query.purpose, amount: query.amount, term_months: query.term },
      }),
    enabled: Boolean(company && query.purpose && query.amount > 0),
  });

  const create = useMutation({
    mutationFn: (r: MatchResult) =>
      api<Application>("/api/v1/applications", {
        method: "POST",
        json: { company_id: company!.id, product_code: r.product_code, amount: r.amount, term_months: r.term_months, purpose },
      }),
    onSuccess: (app) => nav(`/c/applications/${app.id}`),
    onError: (e) => toast.error(e),
  });

  return (
    <>
      <div className="crumbs">
        <Link to="/c">Главная</Link>
        <span>/</span>
        <span>Подбор кредита</span>
      </div>
      <div className="page-head">
        <div>
          <h1>Подберём кредит под задачу</h1>
          <p className="muted">Ответьте на два вопроса — покажем подходящие продукты и примерный платёж.</p>
        </div>
      </div>

      <div className="stack">
        <section className="panel">
          <h2>На что нужны деньги?</h2>
          <div className="purpose-grid">
            {dict?.purposes.map((p) => (
              <button key={p.code} className={`purpose ${purpose === p.code ? "on" : ""}`} onClick={() => setPurpose(p.code)} aria-pressed={purpose === p.code}>
                <b>{p.label}</b>
                <span>{p.hint}</span>
              </button>
            ))}
          </div>
        </section>

        <section className="panel">
          <h2>Сколько и на какой срок?</h2>
          <div className="form-grid">
            <div className="field">
              <label htmlFor="amount">Сумма</label>
              <div className="input-group">
                <input
                  id="amount"
                  className="input input-money"
                  inputMode="numeric"
                  value={amountText}
                  placeholder="1 000 000"
                  onChange={(e) => setAmountText(formatInputAmount(e.target.value))}
                />
                <span className="suffix">₽</span>
              </div>
              <div className="presets">
                {PRESETS.map((v) => (
                  <button key={v} onClick={() => setAmountText(formatInputAmount(v))}>
                    {moneyShort(v)}
                  </button>
                ))}
              </div>
            </div>
            <div className="field">
              <label htmlFor="term">Срок</label>
              <select id="term" className="select" value={term} onChange={(e) => setTerm(Number(e.target.value))}>
                {TERMS.map((t) => (
                  <option key={t} value={t}>
                    {t} мес.
                  </option>
                ))}
              </select>
              <span className="field-hint">Досрочно погасить можно в любой момент без комиссии</span>
            </div>
          </div>
        </section>

        {(!purpose || amount <= 0) && (
          <p className="muted small">Выберите цель и сумму — варианты появятся ниже.</p>
        )}
        {results.isFetching && !results.data && <Loader text="Подбираем продукты…" />}
        {results.data && (
          <section className="stack">
            <div className="row">
              <h2>Подходящие варианты</h2>
              <span className="spacer" />
              <span className="small muted">Ставки рассчитаны от ключевой ставки {pct(results.data.key_rate, 2)}</span>
            </div>
            {results.data.results.length === 0 && (
              <div className="panel-flat">Для этой цели пока нет подходящих продуктов — выберите другую цель.</div>
            )}
            {results.data.results.map((r) => (
              <article key={r.product_code} className={`option ${r.recommended ? "recommended" : ""}`}>
                <div>
                  <h3>
                    {r.product_name}
                    {r.recommended && <span className="tag">Подходит лучше всего</span>}
                  </h3>
                  <p className="small muted" style={{ marginTop: 4 }}>
                    {r.tagline}
                  </p>
                  <div className="row-wrap" style={{ marginTop: 8 }}>
                    {r.features.map((f) => (
                      <span key={f} className="tag-plain">
                        {f}
                      </span>
                    ))}
                  </div>
                  {r.notes.length > 0 && (
                    <ul className="small" style={{ margin: "10px 0 0", paddingLeft: 18, color: "var(--ink-2)" }}>
                      {r.notes.map((n) => (
                        <li key={n}>{n}</li>
                      ))}
                    </ul>
                  )}
                  {r.required_documents.length > 0 && (
                    <p className="xs faint" style={{ marginTop: 8 }}>
                      Понадобятся документы: {r.required_documents.map((d) => dict?.doc_types[d] ?? d).join(", ").toLowerCase()}
                    </p>
                  )}
                </div>
                <div>
                  <div className="xs faint">Ставка</div>
                  <div className="val">
                    {pct(r.rate_from)} – {pct(r.rate_to)}
                  </div>
                  <div className="xs faint">точная — после оценки</div>
                </div>
                <div>
                  <div className="xs faint">{r.repayment_type === "REVOLVING" ? "Проценты в месяц при полном использовании" : "Платёж в месяц, примерно"}</div>
                  <div className="val">{money(r.monthly_payment)}</div>
                  <div className="xs faint">
                    {money(r.amount)} на {r.term_months} мес.
                  </div>
                </div>
                <button className={`btn ${r.recommended ? "btn-primary" : "btn-ghost"}`} disabled={create.isPending} onClick={() => create.mutate(r)}>
                  Оформить
                </button>
              </article>
            ))}
          </section>
        )}
      </div>
    </>
  );
}
