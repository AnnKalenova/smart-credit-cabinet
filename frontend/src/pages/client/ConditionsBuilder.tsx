import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, FilePlus2, RotateCcw, Send, Sparkles, UserRoundCheck } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, api } from "../../api/client";
import type { Application, Conditions, SimOutcome, SimProduct, SimResult, Simulation } from "../../api/types";
import { AmountInput, Modal } from "../../components/ui";
import { dateTime, formatInputAmount, money, moneyShort, parseAmount, pct } from "../../lib/format";
import { useToast } from "../../lib/toast";

const TONE: Record<SimOutcome, string> = {
  APPROVE: "positive",
  COUNTER_OFFER: "attention",
  MANUAL_REVIEW: "progress",
  NEEDS_INFO: "attention",
  DECLINE: "negative",
};

const CLOSED = ["DECLINED", "CLIENT_DECLINED", "EXPIRED", "CANCELLED"];
// открытые заявки, которые заменяются новой одним действием
const REPLACEABLE = ["OFFER_READY", "MANUAL_REVIEW"];

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

/** «останется описать залог, подписать согласия и отправить» — по тому, чего не хватает новой заявке. */
function nextSteps(todo: string[]): string {
  return `Данные и документы перенесём — останется ${[...todo, "подписать согласия и отправить"].join(", ")}.`;
}

/** Сроки — радиогруппа: одна остановка по Tab, выбор стрелками. */
function TermChips({ terms, value, label, onChange }: { terms: number[]; value: number; label: string; onChange: (t: number) => void }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const current = Math.max(0, terms.indexOf(value));
  const onKey = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    const last = terms.length - 1;
    const next =
      e.key === "ArrowRight" || e.key === "ArrowDown"
        ? i === last ? 0 : i + 1
        : e.key === "ArrowLeft" || e.key === "ArrowUp"
          ? i === 0 ? last : i - 1
          : e.key === "Home"
            ? 0
            : e.key === "End"
              ? last
              : null;
    if (next === null) return;
    e.preventDefault();
    onChange(terms[next]);
    refs.current[next]?.focus();
  };
  return (
    <div className="term-chips" role="radiogroup" aria-label={label}>
      {terms.map((t, i) => (
        <button
          key={t}
          ref={(el) => {
            refs.current[i] = el;
          }}
          type="button"
          role="radio"
          aria-checked={t === value}
          tabIndex={i === current ? 0 : -1}
          className={t === value ? "on" : ""}
          onClick={() => onChange(t)}
          onKeyDown={(e) => onKey(e, i)}
        >
          {t}
        </button>
      ))}
    </div>
  );
}

function Verdict({ r, stale }: { r: SimResult; stale: boolean }) {
  const revolving = r.repayment_type === "REVOLVING";
  const share = r.payment_share;
  const cap = r.payment_share_cap;
  const over = share !== null && share > cap;
  // шкала до 60%: допустимая граница 40% стоит на двух третях
  const scale = Math.max(0.6, (share ?? 0) * 1.05);
  return (
    <div className={`verdict ${TONE[r.outcome]} ${stale ? "stale" : ""}`}>
      <div className="verdict-title">{r.verdict}</div>
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
            <div className="k">{revolving ? "Проценты за год при полном использовании" : "Проценты за весь срок"}</div>
            <div className="v">{money(r.total_interest)}</div>
          </div>
          {r.max_amount ? (
            <div>
              <div className="k">Максимум на этот срок</div>
              <div className="v">{money(r.max_amount)}</div>
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
            <span className="nowrap">граница {pct(cap * 100, 0)}</span>
          </div>
        </div>
      )}
      {r.outcome === "COUNTER_OFFER" && r.offers.length > 0 && (
        <div className="small">
          <span className="muted">Банк предложит:</span>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {r.offers.map((o) => (
              <li key={`${o.product_code}-${o.amount}-${o.term_months}`}>
                {o.product_name}: {money(o.amount)} на {o.term_months}&nbsp;мес. под {pct(o.rate)}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** Доступен ли конструктор для заявки (тот же запрос, что делает сам конструктор, — повторно не выполняется). */
/** Версия заявки для кэша расчётов: статус и варианты банка. После решения специалиста расчёты запрашиваются заново. */
function simVersion(app: Application): string {
  return `${app.status}:${app.offers.map((o) => o.id).join(",")}`;
}

export function useBuilderAvailable(app: Application): boolean | undefined {
  const q = useQuery({
    queryKey: ["simulation", app.id, simVersion(app), "base"],
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
  const resultRef = useRef<HTMLDivElement>(null);

  // первый запрос — без параметров: сервер отвечает условиями заявки и границами продуктов
  const version = simVersion(app);
  const initial = useQuery({
    queryKey: ["simulation", app.id, version, "base"],
    queryFn: () => api<Simulation>(`/api/v1/applications/${app.id}/simulation`, { method: "POST", json: {} }),
    retry: false,
    staleTime: 60_000,
  });
  // условия, которые сотрудник передал руководителю, открываются у руководителя сразу в конструкторе
  const proposal = app.permissions.can_sign ? app.proposed_conditions : null;
  // стартовые условия — один раз, когда пришёл первый расчёт (состояние из ответа, без лишнего эффекта)
  if (initial.data && !cond) {
    const fromProposal = proposal && initial.data.products.some((p) => p.code === proposal.conditions.product_code);
    const start = fromProposal ? proposal.conditions : initial.data.base;
    setCond(start);
    setAmountText(formatInputAmount(start.amount));
  }
  // расчёт для условий заявки уже есть — второй раз за ним не ходим
  useEffect(() => {
    if (initial.data) qc.setQueryData(["simulation", app.id, version, initial.data.base], initial.data);
  }, [initial.data, app.id, version, qc]);

  const products = initial.data?.products ?? [];
  const product = products.find((p) => p.code === cond?.product_code);
  const typed = parseAmount(amountText);
  const amountError =
    product && (typed < product.min_amount || typed > product.max_amount)
      ? `Сумма по продукту — от ${money(product.min_amount)} до ${money(product.max_amount)}`
      : null;

  const debounced = useDebounced(cond, 350);
  const sim = useQuery({
    queryKey: ["simulation", app.id, version, debounced],
    queryFn: () => api<Simulation>(`/api/v1/applications/${app.id}/simulation`, { method: "POST", json: debounced }),
    enabled: Boolean(debounced),
    placeholderData: keepPreviousData,
    retry: false,
    staleTime: 60_000,
  });
  const pending = sim.isFetching || (cond !== null && debounced !== cond);
  // пока ответ не пришёл именно для выбранных условий — прежние цифры приглушены, подать заявку нельзя
  const stale = pending || sim.isPlaceholderData || Boolean(amountError);

  const base = initial.data?.base;
  const changed = Boolean(cond && base && !same(cond, base));
  const live = app.next_application?.is_live ? app.next_application : null;

  if (initial.isError) {
    // 404 и 409 — конструктор для этой заявки недоступен (например, решение принял специалист): не показываем
    if (initial.error instanceof ApiError && [403, 404, 409].includes(initial.error.status)) return null;
    return (
      <section className="panel" aria-labelledby="builder-title">
        <h2 id="builder-title">Конструктор условий</h2>
        <div className="row-wrap" style={{ marginTop: 10 }}>
          <span className="small muted">Не удалось загрузить расчёт — сервис не ответил.</span>
          <button className="btn btn-ghost btn-s" onClick={() => void initial.refetch()}>
            <RotateCcw size={16} /> Повторить
          </button>
        </div>
      </section>
    );
  }
  if (!initial.data || !cond || !product) return null;
  // стоп-фактор (ликвидация, просрочки) условиями не исправить — объяснение уже есть выше
  if (initial.data.result.outcome === "DECLINE" && !initial.data.result.fixable) return null;

  if (live) {
    // новая заявка уже создана — условия меняются в ней, здесь только ссылка
    return (
      <section className="panel" aria-labelledby="builder-title">
        <h2 id="builder-title">Конструктор условий</h2>
        <div className="row-wrap" style={{ marginTop: 10 }}>
          <span className="small muted">
            По этой заявке уже создана новая — №&nbsp;{live.number}, «{live.status_title.toLowerCase()}».
          </span>
          <Link className="btn btn-primary btn-s" to={`/c/applications/${live.id}`}>
            Открыть заявку №&nbsp;{live.number}
          </Link>
        </div>
      </section>
    );
  }

  const r = sim.data?.result;
  const update = (patch: Partial<Conditions>) => setCond((c) => (c ? { ...c, ...patch } : c));

  const setAmount = (text: string) => {
    setAmountText(text);
    const n = parseAmount(text);
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
  const applySuggestion = (params: Conditions) => {
    if (stale) return;
    apply(params);
    // подсказки пересчитываются, нажатая кнопка может исчезнуть — переводим фокус на решение банка
    window.setTimeout(() => resultRef.current?.focus({ preventScroll: true }), 0);
  };

  // кто и как подаёт новую заявку из текущего статуса
  const status = app.status;
  const signers = app.permissions.signers.join(", ") || "руководитель";
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
  const canPropose = !app.permissions.can_sign && REPLACEABLE.includes(status);
  const sent = !app.permissions.can_sign ? app.proposed_conditions : null;
  const alreadySent = Boolean(sent && same(sent.conditions, cond));
  const willDecline = r?.outcome === "DECLINE";
  const sameAsNow = !changed && !CLOSED.includes(status);
  const blocked = busy || stale || sim.isError || !r || willDecline || sameAsNow;

  const refreshApp = () => {
    void qc.invalidateQueries({ queryKey: ["applications"] });
    void qc.invalidateQueries({ queryKey: ["application", app.id] });
  };

  const submit = async () => {
    setBusy(true);
    try {
      // отказ от предложения или отзыв и новая заявка — одной операцией: если что-то не так, текущая заявка не меняется
      const created = await api<Application>(`/api/v1/applications/${app.id}/reapply`, {
        method: "POST",
        json: { ...cond, replace_current: mode !== "reapply", expected_status: status },
      });
      toast.ok(`Создали заявку №\u00a0${created.number}: проверьте данные, подпишите согласия и отправьте`);
      refreshApp();
      nav(`/c/applications/${created.id}`);
    } catch (e) {
      toast.error(e);
      refreshApp();
    } finally {
      setBusy(false);
      setConfirm(false);
    }
  };

  const chooseOffer = async (offerId: string) => {
    setBusy(true);
    try {
      await api(`/api/v1/applications/${app.id}/offers/${offerId}/accept`, { method: "POST" });
      toast.ok("Предложение принято — осталось подписать договор");
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
      refreshApp();
    }
  };

  const propose = async () => {
    setBusy(true);
    try {
      await api(`/api/v1/applications/${app.id}/request-signature`, { method: "POST", json: { conditions: cond } });
      toast.ok(`Условия переданы: ${signers}. Они откроются в конструкторе этой заявки`);
      refreshApp();
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
    }
  };

  // ------------------------------------------------------------------ действие внизу
  let action: ReactNode;
  if (sim.isError) {
    action = (
      <div className="row-wrap">
        <button className="btn btn-ghost" onClick={() => void sim.refetch()}>
          <RotateCcw size={18} /> Пересчитать
        </button>
        <span className="small muted">Подать заявку можно, когда банк пересчитает решение.</span>
      </div>
    );
  } else if (sameAsNow) {
    action = <p className="small muted">Измените условия — банк сразу пересчитает решение.</p>;
  } else if (r?.offer && !stale) {
    action = app.permissions.can_accept ? (
      <div className="row-wrap">
        <button className="btn btn-signal" disabled={busy} onClick={() => chooseOffer(r.offer!.id)}>
          <BadgeCheck size={18} /> Выбрать этот вариант
        </button>
        <span className="small muted">Это вариант из предложения банка — новая заявка не нужна.</span>
      </div>
    ) : (
      <p className="small muted">Это вариант из предложения банка. Выбрать его может {signers}.</p>
    );
  } else if (mode) {
    const primary = status !== "OFFER_READY"; // при действующем предложении главное действие — выбрать вариант выше
    action = (
      <div className="row-wrap">
        <button className={`btn ${primary ? "btn-signal" : "btn-primary"}`} disabled={blocked} onClick={() => (mode === "reapply" ? submit() : setConfirm(true))}>
          <FilePlus2 size={18} /> Подать заявку на этих условиях
        </button>
        <span className="small muted">
          {amountError
            ? "Укажите сумму в пределах продукта — банк пересчитает решение."
            : willDecline
              ? (sim.data?.suggestions.length ?? 0) > 0
                ? "На этих условиях банк не одобрит кредит — попробуйте подсказки выше."
                : "На этих условиях банк не одобрит кредит — попробуйте меньшую сумму или больший срок."
              : nextSteps(r?.todo ?? [])}
        </span>
      </div>
    );
  } else if (canPropose) {
    action = (
      <div className="row-wrap">
        <button className="btn btn-primary" disabled={blocked || alreadySent} onClick={propose}>
          <Send size={18} /> Передать руководителю
        </button>
        <span className="small muted">
          {alreadySent
            ? `Эти условия уже переданы: ${signers}.`
            : `Подать заявку на других условиях может ${signers}. Передадим выбранные условия — они откроются в этом конструкторе.`}
        </span>
      </div>
    );
  } else {
    action = <p className="small muted">Подать заявку на других условиях может {signers}.</p>;
  }

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

      {sent && (
        <div className="callout info" style={{ marginTop: 16 }}>
          <Send size={18} />
          <span>
            {sent.by ?? "Сотрудник"} передал(а) руководителю условия: {sent.text} ({dateTime(sent.at)}).
          </span>
        </div>
      )}

      {proposal && (
        <div className="callout info" style={{ marginTop: 16 }}>
          <UserRoundCheck size={18} />
          <div className="stack-s">
            <span>
              {proposal.by ?? "Сотрудник"} предлагает подать новую заявку: {proposal.text} ({dateTime(proposal.at)}).
            </span>
            {!same(cond, proposal.conditions) && (
              <div>
                <button className="btn btn-ghost btn-s" onClick={() => apply(proposal.conditions)}>
                  Показать эти условия
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="builder">
        <div className="builder-grid">
          <div className="builder-controls">
            <div className="controls-col">
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
                  <AmountInput
                    id="builder-amount"
                    className="input input-money"
                    value={amountText}
                    aria-invalid={Boolean(amountError)}
                    aria-describedby={amountError ? "builder-amount-error" : undefined}
                    onValueChange={setAmount}
                  />
                  <span className="suffix">₽</span>
                </div>
                <input
                  className="range"
                  type="range"
                  aria-label={product.repayment_type === "REVOLVING" ? "Лимит, ползунок" : "Сумма, ползунок"}
                  aria-valuetext={money(cond.amount)}
                  min={product.min_amount}
                  max={product.max_amount}
                  step={product.max_amount > 20_000_000 ? 500_000 : 50_000}
                  value={Math.min(Math.max(cond.amount, product.min_amount), product.max_amount)}
                  onChange={(e) => setAmount(formatInputAmount(Number(e.target.value)))}
                />
                <div className="range-scale">
                  <span>{moneyShort(product.min_amount)}</span>
                  <span>{moneyShort(product.max_amount)}</span>
                </div>
                {amountError && (
                  <span className="field-error" id="builder-amount-error" role="alert">
                    {amountError}
                  </span>
                )}
              </div>
            </div>
            <div className="controls-col">
              <div className="field">
                <span className="field-label" id="builder-term">
                  {product.repayment_type === "REVOLVING" ? "Срок действия лимита, мес." : "Срок, мес."}
                </span>
                <TermChips
                  terms={product.terms}
                  value={cond.term_months}
                  label={product.repayment_type === "REVOLVING" ? "Срок действия лимита, месяцев" : "Срок, месяцев"}
                  onChange={(t) => update({ term_months: t })}
                />
              </div>

              <div className="switches">
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
          </div>

          <div className="builder-result">
            <div className="result-caption" aria-live="polite" ref={resultRef} tabIndex={-1}>
              <span>Решение банка на этих условиях</span>
              {pending && !sim.isError && (
                <span className="recalc">
                  <span className="spin" aria-hidden /> пересчитываем
                </span>
              )}
            </div>
            {sim.isError ? (
              <div className="verdict negative" role="alert">
                <div className="verdict-title">Не удалось пересчитать решение</div>
                <p className="small muted">
                  {sim.error instanceof ApiError && sim.error.status === 429 ? sim.error.message : "Сервис не ответил."} Условия, которые вы
                  выбрали, сохранены — нажмите «Пересчитать».
                </p>
              </div>
            ) : r ? (
              <Verdict r={r} stale={stale} />
            ) : (
              <div className="verdict">
                <span className="recalc">
                  <span className="spin" aria-hidden /> считаем решение
                </span>
              </div>
            )}
            {!sim.isError && sim.data && sim.data.suggestions.length > 0 && (
              <div className="suggestions">
                <div className="row" style={{ gap: 8, marginBottom: 10 }}>
                  <Sparkles size={17} style={{ color: "var(--accent)" }} />
                  <b>Что улучшит условия</b>
                </div>
                <div>
                  {sim.data.suggestions.map((s) => (
                    <div key={s.code} className="suggestion">
                      <div>
                        <b>{s.title}</b>
                        <br />
                        <span>{s.effect}</span>
                      </div>
                      <button className="btn btn-ghost btn-s" aria-disabled={stale} onClick={() => applySuggestion(s.params)}>
                        Применить
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      <hr className="rule" />
      {action}

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
            на выбранных условиях: {product.name.toLowerCase()}, {money(cond.amount)} на {cond.term_months}&nbsp;мес. {nextSteps(r?.todo ?? [])}
          </p>
        </Modal>
      )}
    </section>
  );
}
