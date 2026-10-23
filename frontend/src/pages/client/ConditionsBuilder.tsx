import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { FilePlus2, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, api } from "../../api/client";
import type { Application, Conditions, SimOutcome, SimProduct, SimResult, Simulation } from "../../api/types";
import { Modal } from "../../components/ui";
import { formatInputAmount, money, moneyShort, parseAmount, pct } from "../../lib/format";
import { useToast } from "../../lib/toast";

const TONE: Record<SimOutcome, string> = {
  APPROVE: "positive",
  COUNTER_OFFER: "attention",
  MANUAL_REVIEW: "progress",
  NEEDS_INFO: "attention",
  DECLINE: "negative",
};

const CLOSED = ["DECLINED", "CLIENT_DECLINED", "EXPIRED", "CANCELLED"];

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return v;
}

function same(a: Conditions, b: Conditions): boolean {
  return (
    a.product_code === b.product_code &&
    a.amount === b.amount &&
    a.term_months === b.term_months &&
    a.has_collateral === b.has_collateral &&
    a.owner_guarantee === b.owner_guarantee
  );
}

/** Условия под продукт: сумма и срок — в его границах, залог — по его правилам. */
function fitToProduct(c: Conditions, p: SimProduct): Conditions {
  const amount = Math.min(Math.max(c.amount, p.min_amount), p.max_amount);
  const term = p.terms.includes(c.term_months)
    ? c.term_months
    : p.terms.reduce((best, t) => (Math.abs(t - c.term_months) < Math.abs(best - c.term_months) ? t : best), p.terms[0]);
  const has_collateral = p.collateral === "REQUIRED" ? true : p.collateral === "NONE" ? false : c.has_collateral;
  return { ...c, product_code: p.code, amount, term_months: term, has_collateral };
}

function intro(status: string): string {
  if (status === "OFFER_READY") return "Хотите другую сумму или срок? Меняйте условия — банк сразу пересчитает решение по тем же правилам, что и для этой заявки.";
  if (status === "MANUAL_REVIEW") return "Пока специалист изучает заявку, посмотрите, при каких условиях банк решит автоматически.";
  if (status === "DECLINED") return "Подберите условия, при которых банк сможет одобрить кредит, и подайте новую заявку в один клик.";
  return "Подберите условия для новой заявки — банк сразу покажет решение, ставку и платёж.";
}

function Verdict({ r, pending }: { r: SimResult; pending: boolean }) {
  const revolving = r.repayment_type === "REVOLVING";
  const share = r.payment_share;
  const cap = r.payment_share_cap;
  const over = share !== null && share > cap;
  // шкала до 60%: допустимая граница 40% стоит на двух третях
  const scale = Math.max(0.6, (share ?? 0) * 1.05);
  return (
    <div className={`verdict ${TONE[r.outcome]}`} aria-live="polite">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <div className="verdict-title">{r.verdict}</div>
        {pending && (
          <span className="recalc">
            <span className="spin" /> пересчитываем
          </span>
        )}
      </div>
      <p className="small muted">{r.explanation}</p>
      {r.outcome !== "DECLINE" && (
        <div className="figures">
          <div>
            <div className="k">Ставка</div>
            <div className="v">{pct(r.rate)}</div>
          </div>
          <div>
            <div className="k">{revolving ? "Проценты в месяц при полном использовании" : "Платёж в месяц"}</div>
            <div className="v">{money(r.monthly_payment)}</div>
          </div>
          <div>
            <div className="k">{revolving ? "Проценты за год при полном использовании" : "Переплата за весь срок"}</div>
            <div className="v">{money(r.total_interest)}</div>
          </div>
          {r.max_amount ? (
            <div>
              <div className="k">Максимум на этот срок</div>
              <div className="v">{moneyShort(r.max_amount)}</div>
            </div>
          ) : null}
        </div>
      )}
      {share !== null && !revolving && (
        <div>
          <div className="small">
            Платежи по всем кредитам — <b>{pct(share * 100, 0)}</b> поступлений на счёт
          </div>
          <div className={`meter ${over ? "over" : ""}`} role="img" aria-label={`Нагрузка ${pct(share * 100, 0)} при допустимой ${pct(cap * 100, 0)}`}>
            <span style={{ width: `${Math.min(100, (share / scale) * 100)}%` }} />
            <i style={{ left: `${(cap / scale) * 100}%` }} />
          </div>
          <div className="meter-caption">
            <span>{over ? "Выше допустимой нагрузки" : "В пределах допустимой нагрузки"}</span>
            <span>граница {pct(cap * 100, 0)}</span>
          </div>
        </div>
      )}
      {r.outcome === "COUNTER_OFFER" && r.offers.length > 0 && (
        <div className="small">
          <span className="muted">Банк предложит:</span>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {r.offers.map((o) => (
              <li key={`${o.product_code}-${o.amount}-${o.term_months}`}>
                {o.product_name}: {money(o.amount)} на {o.term_months} мес. под {pct(o.rate)}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** Доступен ли конструктор для заявки (тот же запрос, что делает сам конструктор, — повторно не выполняется). */
export function useBuilderAvailable(app: Application): boolean | undefined {
  const q = useQuery({
    queryKey: ["simulation", app.id, "base"],
    queryFn: () => api<Simulation>(`/api/v1/applications/${app.id}/simulation`, { method: "POST", json: {} }),
    retry: false,
    staleTime: 60_000,
  });
  if (q.isLoading) return undefined;
  if (!q.data) return false;
  return !(q.data.result.outcome === "DECLINE" && !q.data.result.fixable);
}

export function ConditionsBuilder({ app }: { app: Application }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const toast = useToast();
  const [cond, setCond] = useState<Conditions | null>(null);
  const [amountText, setAmountText] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);

  // первый запрос — без параметров: сервер отвечает условиями заявки и границами продуктов
  const initial = useQuery({
    queryKey: ["simulation", app.id, "base"],
    queryFn: () => api<Simulation>(`/api/v1/applications/${app.id}/simulation`, { method: "POST", json: {} }),
    retry: false,
    staleTime: 60_000,
  });
  useEffect(() => {
    if (initial.data && !cond) {
      setCond(initial.data.base);
      setAmountText(formatInputAmount(initial.data.base.amount));
      qc.setQueryData(["simulation", app.id, initial.data.base], initial.data);
    }
  }, [initial.data, cond, app.id, qc]);

  const products = initial.data?.products ?? [];
  const product = products.find((p) => p.code === cond?.product_code);
  const typed = parseAmount(amountText);
  const amountError =
    product && (typed < product.min_amount || typed > product.max_amount)
      ? `Сумма по продукту — от ${money(product.min_amount)} до ${money(product.max_amount)}`
      : null;

  const debounced = useDebounced(cond, 350);
  const sim = useQuery({
    queryKey: ["simulation", app.id, debounced],
    queryFn: () => api<Simulation>(`/api/v1/applications/${app.id}/simulation`, { method: "POST", json: debounced }),
    enabled: Boolean(debounced),
    placeholderData: keepPreviousData,
    retry: false,
    staleTime: 60_000,
  });
  const data = sim.data ?? initial.data;
  const pending = sim.isFetching || (cond !== null && debounced !== cond);

  const base = initial.data?.base;
  const changed = Boolean(cond && base && !same(cond, base));
  const live = app.next_application?.is_live ? app.next_application : null;

  const termStep = useMemo(() => (product ? product.terms : []), [product]);

  if (initial.error) {
    // конструктор недоступен (например, заявку не успели оценить) — просто не показываем его
    if (initial.error instanceof ApiError && [404, 409].includes(initial.error.status)) return null;
    return null;
  }
  if (!initial.data || !cond || !product || !data) return null;
  // стоп-фактор (ликвидация, просрочки) условиями не исправить — объяснение уже есть выше
  if (initial.data.result.outcome === "DECLINE" && !initial.data.result.fixable) return null;

  const r = data.result;
  const update = (patch: Partial<Conditions>) => setCond((c) => (c ? { ...c, ...patch } : c));

  const setAmount = (text: string) => {
    const formatted = formatInputAmount(text);
    setAmountText(formatted);
    const n = parseAmount(formatted);
    if (n >= product.min_amount && n <= product.max_amount) update({ amount: n });
  };
  const chooseProduct = (code: string) => {
    const p = products.find((x) => x.code === code);
    if (!p) return;
    const next = fitToProduct(cond, p);
    setCond(next);
    setAmountText(formatInputAmount(next.amount));
  };
  const apply = (params: Conditions) => {
    setCond(params);
    setAmountText(formatInputAmount(params.amount));
  };

  // кто и как подаёт новую заявку из текущего статуса
  const status = app.status;
  const mode: "reapply" | "decline" | "cancel" | null = CLOSED.includes(status)
    ? app.permissions.can_reapply
      ? "reapply"
      : null
    : status === "OFFER_READY"
      ? app.permissions.can_accept
        ? "decline"
        : null
      : status === "MANUAL_REVIEW"
        ? app.permissions.can_cancel
          ? "cancel"
          : null
        : null;
  const willDecline = r.outcome === "DECLINE";
  const sameAsNow = !changed && !CLOSED.includes(status);

  const submit = async () => {
    if (!cond) return;
    setBusy(true);
    try {
      if (mode === "decline") await api(`/api/v1/applications/${app.id}/offers/decline`, { method: "POST" });
      if (mode === "cancel") await api(`/api/v1/applications/${app.id}/cancel`, { method: "POST" });
      const created = await api<Application>(`/api/v1/applications/${app.id}/reapply`, { method: "POST", json: cond });
      toast.ok(`Создали заявку № ${created.number}: проверьте данные, подпишите согласия и отправьте`);
      void qc.invalidateQueries({ queryKey: ["applications"] });
      void qc.invalidateQueries({ queryKey: ["application", app.id] });
      nav(`/c/applications/${created.id}`);
    } catch (e) {
      toast.error(e);
      void qc.invalidateQueries({ queryKey: ["application", app.id] });
    } finally {
      setBusy(false);
      setConfirm(false);
    }
  };

  return (
    <section className="panel" aria-labelledby="builder-title">
      <div className="row" style={{ alignItems: "flex-start" }}>
        <div className="stack-s" style={{ flex: 1 }}>
          <h2 id="builder-title">Конструктор условий</h2>
          <p className="muted small">{intro(status)}</p>
        </div>
        {changed && (
          <button className="btn btn-ghost btn-s" onClick={() => base && apply(base)}>
            Как в заявке
          </button>
        )}
      </div>

      <div className="builder">
        <div className="builder-controls">
          {products.length > 1 && (
            <label className="field">
              <span className="field-label">Продукт</span>
              <select className="select" value={cond.product_code} onChange={(e) => chooseProduct(e.target.value)}>
                {products.map((p) => (
                  <option key={p.code} value={p.code}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          )}

          <div className="field">
            <label className="field-label" htmlFor="builder-amount">
              {product.repayment_type === "REVOLVING" ? "Лимит" : "Сумма"}
            </label>
            <div className="input-group">
              <input
                id="builder-amount"
                className="input input-money"
                inputMode="numeric"
                maxLength={17}
                value={amountText}
                aria-invalid={Boolean(amountError)}
                onChange={(e) => setAmount(e.target.value)}
              />
              <span className="suffix">₽</span>
            </div>
            <input
              className="range"
              type="range"
              aria-label="Сумма, ползунок"
              min={product.min_amount}
              max={product.max_amount}
              step={product.max_amount > 20_000_000 ? 500_000 : 50_000}
              value={Math.min(Math.max(cond.amount, product.min_amount), product.max_amount)}
              onChange={(e) => setAmount(e.target.value)}
            />
            <div className="range-scale">
              <span>{moneyShort(product.min_amount)}</span>
              <span>{moneyShort(product.max_amount)}</span>
            </div>
            {amountError && <span className="field-error">{amountError}</span>}
          </div>

          <div className="field">
            <span className="field-label">{product.repayment_type === "REVOLVING" ? "Срок действия лимита, мес." : "Срок, мес."}</span>
            <div className="term-chips" role="radiogroup" aria-label="Срок">
              {termStep.map((t) => (
                <button key={t} role="radio" aria-checked={cond.term_months === t} className={cond.term_months === t ? "on" : ""} onClick={() => update({ term_months: t })}>
                  {t}
                </button>
              ))}
            </div>
          </div>

          <div className="stack-s" style={{ gap: 14 }}>
            {product.collateral !== "NONE" && (
              <label className="switch">
                <input
                  type="checkbox"
                  checked={cond.has_collateral}
                  disabled={product.collateral === "REQUIRED"}
                  onChange={(e) => update({ has_collateral: e.target.checked })}
                />
                <span>
                  Залог имущества
                  <small>{product.collateral === "REQUIRED" ? "Покупаемое имущество — в залоге, ставка ниже на 1 п.п." : "Ставка ниже на 1 п.п."}</small>
                </span>
              </label>
            )}
            <label className="switch">
              <input type="checkbox" checked={cond.owner_guarantee} onChange={(e) => update({ owner_guarantee: e.target.checked })} />
              <span>
                Поручительство собственника
                <small>Ставка ниже на 0,5 п.п.</small>
              </span>
            </label>
          </div>
        </div>

        <div className="stack">
          <Verdict r={r} pending={pending} />
          {data.suggestions.length > 0 && (
            <div>
              <div className="row" style={{ gap: 8, marginBottom: 10 }}>
                <Sparkles size={17} style={{ color: "var(--accent)" }} />
                <b>Что улучшит условия</b>
              </div>
              <div>
                {data.suggestions.map((s) => (
                  <div key={s.code} className="suggestion">
                    <div>
                      <b>{s.title}</b>
                      <br />
                      <span>{s.effect}</span>
                    </div>
                    <button className="btn btn-ghost btn-s" onClick={() => apply(s.params)}>
                      Применить
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      <hr className="rule" />
      {live ? (
        <div className="row-wrap">
          <span className="small">
            По этой заявке уже создана новая — № {live.number}, «{live.status_title.toLowerCase()}».
          </span>
          <Link className="btn btn-primary btn-s" to={`/c/applications/${live.id}`}>
            Открыть заявку № {live.number}
          </Link>
        </div>
      ) : mode ? (
        <div className="row-wrap">
          <button
            className="btn btn-signal"
            disabled={busy || pending || Boolean(amountError) || willDecline || sameAsNow}
            onClick={() => (mode === "reapply" ? submit() : setConfirm(true))}
          >
            <FilePlus2 size={18} /> Подать заявку на эти условия
          </button>
          <span className="small muted">
            {willDecline
              ? "На этих условиях банк не одобрит кредит — попробуйте подсказки выше."
              : sameAsNow
                ? "Измените условия, чтобы подать новую заявку."
                : "Данные компании и документы перенесём — останется подписать согласия и отправить."}
          </span>
        </div>
      ) : (
        <p className="small muted">
          Подать заявку на другие условия может {app.permissions.signers.join(", ") || "руководитель"}.
        </p>
      )}

      {confirm && (
        <Modal
          title="Подать новую заявку?"
          onClose={() => setConfirm(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setConfirm(false)}>
                Оставить текущую
              </button>
              <button className="btn btn-signal" disabled={busy} onClick={submit}>
                Подать новую заявку
              </button>
            </>
          }
        >
          <p>
            {mode === "decline"
              ? "Текущее предложение банка закроется, и мы создадим новую заявку"
              : "Рассмотрение текущей заявки прекратится, и мы создадим новую"}{" "}
            на выбранных условиях: {product.name.toLowerCase()}, {money(cond.amount)} на {cond.term_months} мес. Данные и документы
            перенесём — останется подписать согласия и отправить.
          </p>
        </Modal>
      )}
    </section>
  );
}
