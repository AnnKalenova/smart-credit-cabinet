import { useQuery } from "@tanstack/react-query";
import { CircleCheck, Send, ShieldCheck, UserRoundCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "../../api/client";
import type { Application, CompanyProfile, MatchResult, Product } from "../../api/types";
import { SignDialog } from "../../components/SignDialog";
import { AmountInput, Field, ProblemList } from "../../components/ui";
import { useAuth } from "../../lib/auth";
import { date, formatInputAmount, money, parseAmount, rateRange } from "../../lib/format";
import { useToast } from "../../lib/toast";
import { DocumentsBlock, useRefreshApp } from "./DocumentsBlock";

type Draft = {
  product_code: string;
  amount: string;
  term_months: number;
  purpose: string;
  purpose_details: string;
  declared_revenue: string;
  has_collateral: boolean;
  collateral_description: string;
  owner_guarantee: boolean;
  contact_name: string;
  contact_phone: string;
};

function toDraft(a: Application): Draft {
  return {
    product_code: a.product_code,
    amount: formatInputAmount(a.amount),
    term_months: a.term_months,
    purpose: a.purpose,
    purpose_details: a.purpose_details ?? "",
    declared_revenue: a.declared_revenue ? formatInputAmount(a.declared_revenue) : "",
    has_collateral: a.has_collateral,
    collateral_description: a.collateral_description ?? "",
    owner_guarantee: a.owner_guarantee,
    contact_name: a.contact_name ?? "",
    contact_phone: a.contact_phone ?? "",
  };
}

const MAX_AMOUNT = 10_000_000_000;

function termOptions(p: Product): number[] {
  const nice = [3, 6, 9, 12, 18, 24, 30, 36, 48, 60, 72, 84];
  const list = nice.filter((t) => t >= p.min_term && t <= p.max_term);
  return list.length ? list : [p.min_term];
}

export function DraftEditor({ app }: { app: Application }) {
  const { dict, company } = useAuth();
  const toast = useToast();
  const refresh = useRefreshApp(app.id);
  const [d, setD] = useState<Draft>(() => toDraft(app));
  const [saved, setSaved] = useState<"idle" | "saving" | "saved">("idle");
  const [problems, setProblems] = useState<string[]>([]);
  const [signing, setSigning] = useState(false);
  const [busy, setBusy] = useState(false);
  const dirty = useRef<Partial<Record<keyof Draft, unknown>>>({});
  const timer = useRef<number | undefined>(undefined);

  const products = useQuery({ queryKey: ["products"], queryFn: () => api<Product[]>("/api/v1/products"), staleTime: 60_000 });
  const profile = useQuery({
    queryKey: ["company", company?.id],
    queryFn: () => api<CompanyProfile>(`/api/v1/companies/${app.company_id}`),
  });
  const product = products.data?.find((p) => p.code === d.product_code) ?? app.product;

  // Если заявку обновили извне (другая вкладка, документы) — подтягиваем поля, которые не редактируются сейчас
  useEffect(() => {
    if (Object.keys(dirty.current).length === 0) setD(toDraft(app));
  }, [app]);

  const flush = async () => {
    const patch = dirty.current;
    if (Object.keys(patch).length === 0) return;
    dirty.current = {};
    setSaved("saving");
    try {
      await api(`/api/v1/applications/${app.id}`, { method: "PATCH", json: patch });
      setSaved("saved");
      refresh();
    } catch (e) {
      setSaved("idle");
      toast.error(e);
    }
  };

  const set = <K extends keyof Draft>(key: K, value: Draft[K], immediate = false) => {
    setD((prev) => ({ ...prev, [key]: value }));
    if (key === "amount") {
      // сумма обязательна: пустое или нулевое значение не сохраняем, пока клиент не введёт число
      const n = parseAmount(String(value));
      if (n > 0 && n <= MAX_AMOUNT) dirty.current.amount = n;
      else delete dirty.current.amount;
    } else if (key === "declared_revenue") {
      dirty.current.declared_revenue = parseAmount(String(value)) || null;
    } else {
      dirty.current[key] = value;
    }
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(flush, immediate ? 0 : 700);
  };

  const changeProduct = (code: string) => {
    const p = products.data?.find((x) => x.code === code);
    if (!p) return;
    set("product_code", code, true);
    const terms = termOptions(p);
    if (!terms.includes(d.term_months)) set("term_months", terms.reduce((a, b) => (Math.abs(b - d.term_months) < Math.abs(a - d.term_months) ? b : a)), true);
    if (!p.purposes.includes(d.purpose)) set("purpose", p.purposes[0], true);
    // залог зависит от продукта: сервер выставляет флаг сам, здесь — чтобы форма сразу совпадала
    if (p.collateral === "REQUIRED") setD((prev) => ({ ...prev, has_collateral: true }));
    if (p.collateral === "NONE") setD((prev) => ({ ...prev, has_collateral: false }));
  };

  const consentsSigned = app.consents.length >= 2;
  const canSign = app.permissions.can_sign;

  const signConsents = async (challengeId: string, code: string) => {
    await flush();
    await api(`/api/v1/applications/${app.id}/consents`, { method: "POST", json: { challenge_id: challengeId, code } });
    toast.ok("Согласия подписаны");
    refresh();
  };

  const submit = async () => {
    setBusy(true);
    setProblems([]);
    try {
      await flush();
      await api(`/api/v1/applications/${app.id}/submit`, { method: "POST" });
      toast.ok("Заявка отправлена в банк");
      refresh();
    } catch (e) {
      if (e instanceof ApiError && e.problems.length) setProblems(e.problems);
      else toast.error(e);
    } finally {
      setBusy(false);
    }
  };

  const askSigner = async () => {
    try {
      await flush();
      await api(`/api/v1/applications/${app.id}/request-signature`, { method: "POST" });
      toast.ok(`Уведомили: ${app.permissions.signers.join(", ")}`);
      refresh();
    } catch (e) {
      toast.error(e);
    }
  };

  const amount = parseAmount(d.amount);
  // пока в поле нет корректной суммы, отправлять нечего: на сервере осталась бы прежняя
  const amountValid = amount > 0 && amount <= MAX_AMOUNT;
  const p = profile.data;

  return (
    <div className="stack">
      <section className="panel">
        <div className="panel-head">
          <h2>Параметры кредита</h2>
          <span className="xs faint" aria-live="polite">
            {saved === "saving" ? "Сохраняем…" : saved === "saved" ? "Черновик сохранён" : "Черновик сохраняется автоматически"}
          </span>
        </div>
        <div className="form-grid">
          <Field label="Продукт">
            <select className="select" value={d.product_code} onChange={(e) => changeProduct(e.target.value)}>
              {(products.data ?? [app.product]).map((p) => (
                <option key={p.code} value={p.code}>
                  {p.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Цель">
            <select className="select" value={d.purpose} onChange={(e) => set("purpose", e.target.value, true)}>
              {product.purposes.map((code) => (
                <option key={code} value={code}>
                  {dict?.purposes.find((x) => x.code === code)?.label ?? code}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label="Сумма"
            hint={`От ${money(product.min_amount)} до ${money(product.max_amount)}`}
            error={
              !amount
                ? "Укажите сумму"
                : amount < product.min_amount || amount > product.max_amount
                  ? `Сумма должна быть от ${money(product.min_amount)} до ${money(product.max_amount)}`
                  : undefined
            }
          >
            <div className="input-group">
              <AmountInput className="input input-money" value={d.amount} onValueChange={(v) => set("amount", v)} />
              <span className="suffix">₽</span>
            </div>
          </Field>
          <Field label={product.repayment_type === "REVOLVING" ? "Срок действия лимита" : "Срок"}>
            <select className="select" value={d.term_months} onChange={(e) => set("term_months", Number(e.target.value), true)}>
              {termOptions(product).map((t) => (
                <option key={t} value={t}>
                  {t} мес.
                </option>
              ))}
            </select>
          </Field>
          <div className="span-2">
            <Field label="Коротко о задаче" hint="Поможет специалисту, если заявка попадёт на ручное рассмотрение">
              <textarea
                className="textarea"
                maxLength={2000}
                value={d.purpose_details}
                placeholder="Например: закупка сырья под заказ сети магазинов"
                onChange={(e) => set("purpose_details", e.target.value)}
              />
            </Field>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{company?.kind === "IP" ? "Предприниматель" : "Компания"}</h2>
          <span className="xs faint">Эти данные банк уже знает — заполнять не нужно</span>
        </div>
        {p && (
          <dl className="kv">
            <dt>Наименование</dt>
            <dd>{p.full_name}</dd>
            <dt>ИНН / {p.kind === "IP" ? "ОГРНИП" : "ОГРН"}</dt>
            <dd>
              {p.inn} / {p.ogrn}
            </dd>
            <dt>Адрес</dt>
            <dd>{p.legal_address}</dd>
            <dt>Дата регистрации</dt>
            <dd>{date(p.registration_date)}</dd>
            <dt>Счёт для зачисления</dt>
            <dd>{p.settlement_account}</dd>
          </dl>
        )}
        <hr className="rule" />
        <div className="form-grid">
          <Field
            label="Выручка за последние 12 месяцев"
            hint={p ? `Мы подставили оценку по поступлениям на ваш счёт. Если часть выручки идёт в другой банк — укажите всю.` : undefined}
          >
            <div className="input-group">
              <AmountInput className="input input-money" maxDigits={15} value={d.declared_revenue} onValueChange={(v) => set("declared_revenue", v)} />
              <span className="suffix">₽</span>
            </div>
          </Field>
          <div />
          <Field label="Контактное лицо">
            <input className="input" maxLength={200} value={d.contact_name} onChange={(e) => set("contact_name", e.target.value)} />
          </Field>
          <Field label="Телефон для связи" hint="Только для уточнений — весь процесс идёт в кабинете">
            <input className="input" type="tel" inputMode="tel" maxLength={32} value={d.contact_phone} onChange={(e) => set("contact_phone", e.target.value)} />
          </Field>
        </div>
      </section>

      <section className="panel">
        <h2>Обеспечение</h2>
        <div className="stack" style={{ marginTop: 12 }}>
          {product.collateral === "REQUIRED" && (
            <div className="callout info">
              <ShieldCheck size={18} />
              <span>Покупаемое имущество оформляется в залог — поэтому ставка по этому кредиту ниже.</span>
            </div>
          )}
          {product.collateral === "OPTIONAL" && (
            <label className="check">
              <input type="checkbox" checked={d.has_collateral} onChange={(e) => set("has_collateral", e.target.checked, true)} />
              <span>Готовы предоставить залог (недвижимость, транспорт, оборудование) — снижает ставку на 1 п.п.</span>
            </label>
          )}
          {product.collateral !== "NONE" && (product.collateral === "REQUIRED" || d.has_collateral) && (
            <Field label="Что будет в залоге" hint="Что это, у кого покупаете, примерная стоимость">
              <textarea className="textarea" maxLength={400} value={d.collateral_description} onChange={(e) => set("collateral_description", e.target.value)} />
            </Field>
          )}
          <label className="check">
            <input type="checkbox" checked={d.owner_guarantee} onChange={(e) => set("owner_guarantee", e.target.checked, true)} />
            <span>Собственник бизнеса готов выступить поручителем — снижает ставку на 0,5 п.п.</span>
          </label>
        </div>
      </section>

      <section className="panel">
        <h2>Документы</h2>
        <div style={{ marginTop: 12 }}>
          <DocumentsBlock app={app} editable />
        </div>
      </section>

      <section className="panel">
        <h2>Согласия и отправка</h2>
        <div className="stack" style={{ marginTop: 12 }}>
          {consentsSigned ? (
            <div className="callout ok">
              <CircleCheck size={18} />
              <span>
                Согласия на запрос кредитной истории и обработку данных подписаны: {app.consents[0].signed_by}, {date(app.consents[0].signed_at)}
              </span>
            </div>
          ) : (
            <p className="small muted">
              {company?.kind === "IP"
                ? "Чтобы банк мог запросить кредитную историю и сведения о вашем бизнесе, нужны два согласия. Подписать их можете вы или представитель по доверенности — кодом прямо здесь."
                : "Чтобы банк мог запросить кредитную историю и сведения о компании, нужны два согласия. Подписать их может руководитель или представитель по доверенности — кодом прямо здесь."}
            </p>
          )}
          <ProblemList problems={problems} />
          {canSign ? (
            <div className="row-wrap">
              {!consentsSigned && (
                <button className="btn btn-ghost" onClick={() => setSigning(true)}>
                  <ShieldCheck size={18} /> Подписать согласия
                </button>
              )}
              <button className="btn btn-signal" disabled={!consentsSigned || busy || !amountValid} onClick={submit}>
                <Send size={18} /> Отправить заявку
              </button>
            </div>
          ) : (
            <div className="callout warn">
              <UserRoundCheck size={18} />
              <div className="stack-s">
                <span>
                  Вы можете подготовить заявку и документы. Подписать согласия и отправить заявку может: {app.permissions.signers.join(", ")}.
                </span>
                <div>
                  <button className="btn btn-primary btn-s" onClick={askSigner}>
                    Передать на подпись
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      </section>

      {signing && (
        <SignDialog appId={app.id} purpose="CONSENTS" title="Подписание согласий" onClose={() => setSigning(false)} onConfirm={signConsents} />
      )}
    </div>
  );
}

/** Предварительный расчёт в черновике: меняется вместе с параметрами заявки. */
export function DraftEstimate({ app }: { app: Application }) {
  const est = useQuery({
    queryKey: ["estimate", app.id, app.product_code, app.amount, app.term_months, app.purpose, app.has_collateral, app.owner_guarantee],
    queryFn: () =>
      api<{ key_rate: number; results: MatchResult[] }>("/api/v1/products/match", {
        method: "POST",
        json: {
          company_id: app.company_id,
          purpose: app.purpose,
          amount: app.amount,
          term_months: app.term_months,
          // залог и поручительство снижают ставку — расчёт совпадает с конструктором условий
          has_collateral: app.has_collateral,
          owner_guarantee: app.owner_guarantee,
        },
      }),
  });
  const r = est.data?.results.find((x) => x.product_code === app.product_code);
  const auto = app.amount <= app.product.auto_decision_limit;
  return (
    <section className="panel">
      <h3>Предварительный расчёт</h3>
      {!r && <p className="small muted" style={{ marginTop: 8 }}>Появится, когда параметры будут в пределах условий продукта.</p>}
      {r && (
        <div className="stack-s" style={{ marginTop: 10 }}>
          <div>
            <div className="xs faint">Ставка</div>
            <div className="num" style={{ fontSize: 19 }}>
              {rateRange(r.rate_from, r.rate_to)}
            </div>
          </div>
          <div>
            <div className="xs faint">{r.repayment_type === "REVOLVING" ? "Проценты в месяц при полном использовании" : "Платёж в месяц, примерно"}</div>
            <div className="num" style={{ fontSize: 19 }}>
              {money(r.monthly_payment)}
            </div>
          </div>
          {r.notes.map((n) => (
            <p key={n} className="xs muted">
              {n}
            </p>
          ))}
          <p className="xs faint">
            {auto
              ? "Для такой суммы решение обычно принимается автоматически за пару минут."
              : `Сумма выше ${money(app.product.auto_decision_limit)} — решение примет кредитный специалист, обычно в течение рабочего дня.`}
          </p>
        </div>
      )}
    </section>
  );
}
