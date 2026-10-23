import { useQuery } from "@tanstack/react-query";
import { BadgeCheck, CircleMinus, CirclePlus, FileSignature, FileText, Info, PartyPopper, Undo2, UserRoundCheck } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../../api/client";
import type { Application, Contract, Offer, ScheduleRow } from "../../api/types";
import { SignDialog } from "../../components/SignDialog";
import { Modal } from "../../components/ui";
import { date, money, pct } from "../../lib/format";
import { useToast } from "../../lib/toast";
import { useRefreshApp } from "./DocumentsBlock";

export function ScheduleTable({ rows, revolving }: { rows: ScheduleRow[]; revolving?: boolean }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>№</th>
            <th>Дата</th>
            <th className="r">Платёж</th>
            <th className="r">Основной долг</th>
            <th className="r">Проценты</th>
            <th className="r">{revolving ? "Лимит" : "Остаток долга"}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.n}>
              <td>{r.n}</td>
              <td>{date(r.date)}</td>
              <td className="r nowrap">{money(r.payment, true)}</td>
              <td className="r nowrap">{money(r.principal, true)}</td>
              <td className="r nowrap">{money(r.interest, true)}</td>
              <td className="r nowrap">{money(r.balance, true)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Pricing({ offer }: { offer: Offer }) {
  return (
    <table className="table" style={{ fontSize: 13 }}>
      <tbody>
        {offer.pricing.parts.map((p) => (
          <tr key={p.label}>
            <td>{p.label}</td>
            <td className="r nowrap">
              {p.value > 0 && p.label !== "Ключевая ставка Банка России" ? "+" : ""}
              {pct(p.value, 2)}
            </td>
          </tr>
        ))}
        <tr>
          <td>
            <b>Итоговая ставка</b>
          </td>
          <td className="r">
            <b>{pct(offer.rate)}</b>
          </td>
        </tr>
      </tbody>
    </table>
  );
}

export function OfferTicket({
  offer,
  primary,
  action,
}: {
  offer: Offer;
  primary?: boolean;
  action?: React.ReactNode;
}) {
  const [showPricing, setShowPricing] = useState(false);
  const revolving = offer.repayment_type === "REVOLVING";
  return (
    <article className={`ticket ${primary ? "primary" : ""}`}>
      <div className="ticket-head">
        <div>
          <b>{offer.label}</b>
          <div className="xs faint">{offer.product_name}</div>
        </div>
        {offer.is_counter ? <span className="chip action">Встречное предложение</span> : <span className="chip positive">Как вы просили</span>}
      </div>
      <div className="ticket-body">
        <div>
          <div className="k">{revolving ? "Лимит" : "Сумма"}</div>
          <div className="big">{money(offer.amount)}</div>
        </div>
        <div>
          <div className="k">{revolving ? "Срок лимита" : "Срок"}</div>
          <div className="big">{offer.term_months} мес.</div>
        </div>
        <div>
          <div className="k">Ставка</div>
          <div className="big">{pct(offer.rate)}</div>
        </div>
        <div>
          <div className="k">{revolving ? "Проценты в месяц при полном использовании" : "Платёж в месяц"}</div>
          <div className="num" style={{ fontSize: 17 }}>
            {money(offer.monthly_payment)}
          </div>
        </div>
        <div>
          <div className="k">{revolving ? "Проценты за год при полном использовании" : "Проценты за весь срок"}</div>
          <div className="num" style={{ fontSize: 17 }}>
            {money(offer.total_interest)}
          </div>
        </div>
        <div>
          <div className="k">Действует до</div>
          <div>{date(offer.valid_until)}</div>
        </div>
      </div>
      <div className="ticket-foot">
        <ul>
          {offer.conditions.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
        <div className="row-wrap">
          {action}
          <button className="btn-link small" onClick={() => setShowPricing((s) => !s)}>
            {showPricing ? "Скрыть расчёт ставки" : "Из чего складывается ставка"}
          </button>
        </div>
        {showPricing && <Pricing offer={offer} />}
      </div>
    </article>
  );
}

export function DecisionExplain({ app }: { app: Application }) {
  const d = app.decision;
  if (!d) return null;
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{d.outcome === "DECLINE" ? "Почему банк отказал" : "Почему такие условия"}</h2>
        <span className="small muted">{d.kind_label}</span>
      </div>
      <div className="stack">
        {d.message && (
          <div className="callout info">
            <Info size={18} />
            <span>
              <b>Сообщение специалиста:</b> {d.message}
            </span>
          </div>
        )}
        {d.reasons.map((r) => (
          <p key={r}>{r}</p>
        ))}
        {(d.positives.length > 0 || d.negatives.length > 0) && (
          <div className="form-grid">
            <div className="stack-s">
              <span className="small muted">Помогло решению</span>
              {d.positives.map((p) => (
                <div key={p} className="row small" style={{ alignItems: "flex-start" }}>
                  <CirclePlus size={16} className="pos" style={{ flex: "none", marginTop: 2 }} />
                  {p}
                </div>
              ))}
              {d.positives.length === 0 && <span className="small faint">—</span>}
            </div>
            <div className="stack-s">
              <span className="small muted">Снизило оценку</span>
              {d.negatives.map((p) => (
                <div key={p} className="row small" style={{ alignItems: "flex-start" }}>
                  <CircleMinus size={16} className="neg" style={{ flex: "none", marginTop: 2 }} />
                  {p}
                </div>
              ))}
              {d.negatives.length === 0 && <span className="small faint">—</span>}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

/** Подсказка для сотрудника без права подписи: кто может принять решение, и кнопка уведомить его. */
function AskSigner({ app, what }: { app: Application; what: string }) {
  const toast = useToast();
  const refresh = useRefreshApp(app.id);
  const [busy, setBusy] = useState(false);
  const ask = async () => {
    setBusy(true);
    try {
      await api(`/api/v1/applications/${app.id}/request-signature`, { method: "POST" });
      toast.ok(`Уведомили: ${app.permissions.signers.join(", ")}`);
      refresh();
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="callout warn">
      <UserRoundCheck size={18} />
      <div className="stack-s">
        <span>
          {what} может: {app.permissions.signers.join(", ")}.
        </span>
        <div>
          <button className="btn btn-primary btn-s" disabled={busy} onClick={ask}>
            Передать на подпись
          </button>
        </div>
      </div>
    </div>
  );
}

export function OfferChoice({ app }: { app: Application }) {
  const toast = useToast();
  const refresh = useRefreshApp(app.id);
  const [busy, setBusy] = useState(false);
  const [confirmDecline, setConfirmDecline] = useState(false);

  const accept = async (offer: Offer) => {
    if (busy) return;
    setBusy(true);
    try {
      await api(`/api/v1/applications/${app.id}/offers/${offer.id}/accept`, { method: "POST" });
      toast.ok("Предложение принято — осталось подписать договор");
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
      refresh();
    }
  };
  const decline = async () => {
    setBusy(true);
    try {
      await api(`/api/v1/applications/${app.id}/offers/decline`, { method: "POST" });
      setConfirmDecline(false);
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
      refresh();
    }
  };

  return (
    <div className="stack">
      <DecisionExplain app={app} />
      <div className="row">
        <h2>{app.offers.length > 1 ? "Выберите вариант" : "Предложение банка"}</h2>
      </div>
      {!app.permissions.can_accept && app.permissions.can_request_signature && (
        <AskSigner app={app} what="Выбрать вариант и подписать кредитный договор" />
      )}
      {app.offers.map((o, i) => (
        <OfferTicket
          key={o.id}
          offer={o}
          primary={i === 0}
          action={
            app.permissions.can_accept && (
              <button className={`btn ${i === 0 ? "btn-signal" : "btn-ghost"}`} disabled={busy} onClick={() => accept(o)}>
                <BadgeCheck size={18} /> Выбрать этот вариант
              </button>
            )
          }
        />
      ))}
      {app.permissions.can_accept && (
        <div>
          <button className="btn-link small" style={{ color: "var(--ink-2)" }} onClick={() => setConfirmDecline(true)}>
            Отказаться от предложения
          </button>
        </div>
      )}
      {confirmDecline && (
        <Modal
          title="Отказаться от предложения?"
          onClose={() => setConfirmDecline(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setConfirmDecline(false)}>
                Оставить
              </button>
              <button className="btn btn-danger" disabled={busy} onClick={decline}>
                Отказаться
              </button>
            </>
          }
        >
          <p>Заявка будет закрыта. Позже можно подать новую — данные заполнятся автоматически.</p>
        </Modal>
      )}
    </div>
  );
}

export function SigningPanel({ app }: { app: Application }) {
  const toast = useToast();
  const refresh = useRefreshApp(app.id);
  const [signing, setSigning] = useState(false);
  const [preview, setPreview] = useState(false);
  const contract = useQuery({ queryKey: ["contract", app.id], queryFn: () => api<Contract>(`/api/v1/applications/${app.id}/contract`) });
  const offer = app.offers[0];

  const sign = async (challengeId: string, code: string) => {
    try {
      await api(`/api/v1/applications/${app.id}/sign`, { method: "POST", json: { challenge_id: challengeId, code } });
    } catch (e) {
      // 409 — предложение истекло или заявка изменилась: показываем актуальный статус
      if (e instanceof ApiError && e.status === 409) refresh();
      throw e;
    }
    toast.ok(offer?.repayment_type === "REVOLVING" ? "Договор подписан — подключаем овердрафт" : "Договор подписан — перечисляем деньги");
    refresh();
  };
  const back = async () => {
    try {
      await api(`/api/v1/applications/${app.id}/signing/back`, { method: "POST" });
    } catch (e) {
      toast.error(e);
    } finally {
      refresh();
    }
  };

  return (
    <div className="stack">
      {offer && <OfferTicket offer={offer} primary />}
      <section className="panel">
        <h2>Документы к подписанию</h2>
        <div className="stack" style={{ marginTop: 12 }}>
          {contract.data?.documents.map((doc) => (
            <div key={doc.code} className="row small">
              <FileText size={17} className="faint" />
              <span>{doc.title}</span>
            </div>
          ))}
          <div className="row-wrap">
            <button className="btn btn-ghost" onClick={() => setPreview(true)} disabled={!contract.data}>
              Посмотреть договор и график
            </button>
            {app.permissions.can_sign && (
              <button className="btn btn-signal" onClick={() => setSigning(true)}>
                <FileSignature size={18} /> Подписать кодом
              </button>
            )}
          </div>
          {!app.permissions.can_sign && <AskSigner app={app} what="Подписать кредитный договор" />}
          {app.permissions.can_sign && (
            <div>
              <button className="btn-link small" style={{ color: "var(--ink-2)" }} onClick={back}>
                <Undo2 size={14} style={{ verticalAlign: -2 }} /> Вернуться к выбору условий
              </button>
            </div>
          )}
        </div>
      </section>
      {preview && contract.data && (
        <Modal title={`Кредитный договор № ${contract.data.contract_number}`} onClose={() => setPreview(false)} wide>
          <div className="stack">
            <dl className="kv">
              <dt>Заёмщик</dt>
              <dd>
                {contract.data.company.full_name}, ИНН {contract.data.company.inn}
              </dd>
              <dt>Продукт</dt>
              <dd>{contract.data.offer.product_name}</dd>
              <dt>Сумма</dt>
              <dd>{money(contract.data.offer.amount)}</dd>
              <dt>Срок</dt>
              <dd>{contract.data.offer.term_months} мес.</dd>
              <dt>Процентная ставка</dt>
              <dd>{pct(contract.data.offer.rate)} годовых</dd>
              <dt>Зачисление</dt>
              <dd>на расчётный счёт {contract.data.company.settlement_account}</dd>
              <dt>Условия</dt>
              <dd>{contract.data.offer.conditions.join("; ")}</dd>
              <dt>Подписант</dt>
              <dd>{contract.data.signer ? `${contract.data.signer.full_name}, ${contract.data.signer.position_label}` : "—"}</dd>
            </dl>
            <h3>График платежей</h3>
            <ScheduleTable rows={contract.data.schedule} revolving={contract.data.offer.repayment_type === "REVOLVING"} />
            <p className="xs faint">
              {contract.data.offer.repayment_type === "REVOLVING"
                ? "Лимит действует с даты подписания договора. Проценты начисляются только на использованную сумму."
                : "График построен от даты подписания договора. Досрочно погасить кредит можно в любой день без комиссии."}
            </p>
          </div>
        </Modal>
      )}
      {signing && <SignDialog appId={app.id} purpose="CONTRACT" title="Подписание кредитного договора" onClose={() => setSigning(false)} onConfirm={sign} />}
    </div>
  );
}

export function IssuedPanel({ app }: { app: Application }) {
  const loan = app.loan;
  if (!loan) {
    return (
      <section className="panel">
        <div className="row">
          <span className="spin" />
          <span>{app.status_view.now} {app.status_view.next}</span>
        </div>
      </section>
    );
  }
  const revolving = loan.repayment_type === "REVOLVING";
  return (
    <section className="panel">
      <div className="row" style={{ alignItems: "flex-start" }}>
        <PartyPopper size={26} className="pos" />
        <div className="stack-s">
          <h2>{revolving ? "Лимит овердрафта установлен" : "Деньги на расчётном счёте"}</h2>
          <p className="muted">
            {revolving
              ? `Лимит ${money(loan.amount)} подключён к счёту — платежи пройдут, даже если на счёте не хватает денег.`
              : `${money(loan.amount)} зачислены ${date(loan.issued_at)}. Первый платёж ${money(loan.monthly_payment)} — ${date(loan.first_payment_date)}.`}
          </p>
        </div>
      </div>
      <hr className="rule" />
      <dl className="kv">
        <dt>Договор</dt>
        <dd>{loan.contract_number}</dd>
        <dt>Ссудный счёт</dt>
        <dd>{loan.loan_account}</dd>
        <dt>Ставка</dt>
        <dd>{pct(loan.rate)} годовых</dd>
        <dt>Срок</dt>
        <dd>{loan.term_months} мес.</dd>
      </dl>
      <div style={{ marginTop: 16 }}>
        <Link to={`/c/loans/${loan.id}`} className="btn btn-primary">
          Открыть график платежей
        </Link>
      </div>
    </section>
  );
}
