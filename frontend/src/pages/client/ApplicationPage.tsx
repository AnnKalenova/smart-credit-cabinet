import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleCheck, MessageSquareText, RotateCcw, Send, Undo2, UserRoundSearch } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, api } from "../../api/client";
import type { Application, Message } from "../../api/types";
import { Conveyor, Field, Loader, Modal, PROCESSING, ProblemList, StatusChip, Timeline } from "../../components/ui";
import { useAuth } from "../../lib/auth";
import { dateTime, formatInputAmount, money, parseAmount, relativeDue } from "../../lib/format";
import { useToast } from "../../lib/toast";
import { DocumentsBlock, useRefreshApp } from "./DocumentsBlock";
import { DraftEditor, DraftEstimate } from "./DraftEditor";
import { DecisionExplain, IssuedPanel, OfferChoice, SigningPanel } from "./OfferPanels";

// ------------------------------------------------------------------ что проверяет банк прямо сейчас

const PIPELINE = [
  { key: "PRECHECK", title: "Проверка полноты и корректности данных" },
  { key: "EGRUL", title: "Выписка из ЕГРЮЛ / ЕГРИП" },
  { key: "FNS", title: "Проверка в сервисе ФНС «Прозрачный бизнес»" },
  { key: "BKI", title: "Кредитная история в бюро кредитных историй" },
  { key: "SCORING", title: "Расчёт кредитного рейтинга" },
  { key: "DECISION", title: "Подбор суммы, срока и ставки" },
];

function stepState(app: Application, key: string): "done" | "now" | "wait" {
  const order = ["SUBMITTED", "PRECHECK", "ENRICHMENT", "SCORING", "DECISION"];
  const statusIdx = order.indexOf(app.status);
  const detail = app.stage_detail ?? "";
  if (key === "PRECHECK") return statusIdx > 1 ? "done" : "now";
  if (["EGRUL", "FNS", "BKI"].includes(key)) {
    if (statusIdx > 2) return "done";
    if (statusIdx < 2) return "wait";
    const current = detail.includes("ЕГРЮЛ") ? "EGRUL" : detail.includes("ФНС") ? "FNS" : detail.includes("БКИ") || detail.includes("кредитных") ? "BKI" : "EGRUL";
    const seq = ["EGRUL", "FNS", "BKI"];
    return seq.indexOf(key) < seq.indexOf(current) ? "done" : key === current ? "now" : "wait";
  }
  if (key === "SCORING") return statusIdx > 3 ? "done" : statusIdx === 3 ? "now" : "wait";
  return statusIdx === 4 ? "now" : "wait";
}

function ProcessingPanel({ app }: { app: Application }) {
  return (
    <section className="panel">
      <h2>Что проверяет банк</h2>
      <p className="small muted" style={{ marginTop: 6 }}>
        Проверка автоматическая: сведения о компании банк запрашивает сам, без справок от вас.
      </p>
      <div className="stack-s" style={{ marginTop: 14 }}>
        {PIPELINE.map((s) => {
          const st = stepState(app, s.key);
          return (
            <div key={s.key} className="row small" style={{ opacity: st === "wait" ? 0.5 : 1 }}>
              {st === "done" ? <CircleCheck size={18} className="pos" /> : st === "now" ? <span className="spin" style={{ margin: "0 2px" }} /> : <span style={{ width: 18 }} />}
              <span style={{ fontWeight: st === "now" ? 600 : 400 }}>{s.title}</span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function ReviewPanel({ app }: { app: Application }) {
  const due = relativeDue(app.review_due_at);
  return (
    <section className="panel">
      <div className="row" style={{ alignItems: "flex-start" }}>
        <UserRoundSearch size={26} />
        <div className="stack-s">
          <h2>Заявку изучает кредитный специалист</h2>
          <p className="muted">
            По этой заявке решение принимает кредитный специалист банка — например, из-за суммы или особенностей бизнеса.
            {app.review_due_at && ` Ориентировочный срок решения — ${dateTime(app.review_due_at)} (${due.text}).`}
          </p>
          <p className="small muted">Если специалисту понадобятся документы, запрос появится здесь, а уведомление — в колокольчике. Написать специалисту можно в переписке справа.</p>
        </div>
      </div>
    </section>
  );
}

function InfoRequestPanel({ app }: { app: Application }) {
  const toast = useToast();
  const refresh = useRefreshApp(app.id);
  const req = app.info_request!;
  const [comment, setComment] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const docTypes = req.items.filter((i) => i.type === "DOCUMENT" && i.doc_type).map((i) => i.doc_type!) as string[];
  const fieldItems = req.items.filter((i) => i.type === "FIELD");
  const commentItems = req.items.filter((i) => i.type === "COMMENT");

  const send = async () => {
    setBusy(true);
    setProblems([]);
    const payload: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(fields)) {
      payload[k] = ["declared_revenue", "amount"].includes(k) ? parseAmount(v) : k === "term_months" ? Number(v) : v;
    }
    try {
      await api(`/api/v1/applications/${app.id}/info-response`, { method: "POST", json: { comment: comment || null, fields: Object.keys(payload).length ? payload : null } });
      toast.ok("Ответ отправлен — заявка снова в работе");
      refresh();
    } catch (e) {
      if (e instanceof ApiError && e.problems.length) setProblems(e.problems);
      else toast.error(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel" style={{ borderColor: "#e9cf74", boxShadow: "0 0 0 3px var(--signal-soft)" }}>
      <h2>Банку нужны уточнения</h2>
      <p className="muted" style={{ marginTop: 6 }}>
        {req.message} <span className="faint small">({req.requested_by}, {dateTime(req.created_at)})</span>
      </p>
      <div className="stack" style={{ marginTop: 16 }}>
        {req.items.map((i) => (
          <div key={i.code} className="small">
            <b>{i.label}</b>
            {i.why && <div className="muted">{i.why}</div>}
          </div>
        ))}
        {docTypes.length > 0 && <DocumentsBlock app={app} editable extraTypes={docTypes} newSince={req.created_at} />}
        {fieldItems.map((i) => {
          const key = i.field ?? i.code;
          // сумму и срок отправленной заявки меняет только тот, кто вправе подписывать
          const signerOnly = ["amount", "term_months"].includes(key) && !app.permissions.can_sign;
          return (
            <Field key={i.code} label={i.label} hint={signerOnly ? `Изменить сумму или срок может: ${app.permissions.signers.join(", ")}` : undefined}>
              <input
                className="input"
                maxLength={200}
                disabled={signerOnly}
                value={fields[key] ?? ""}
                onChange={(e) => {
                  const v = ["declared_revenue", "amount"].includes(key) ? formatInputAmount(e.target.value) : e.target.value;
                  setFields({ ...fields, [key]: v });
                }}
              />
            </Field>
          );
        })}
        <Field label={commentItems[0]?.label ?? "Комментарий для банка (необязательно)"}>
          <textarea className="textarea" maxLength={4000} value={comment} onChange={(e) => setComment(e.target.value)} />
        </Field>
        <ProblemList problems={problems} />
        <div>
          <button className="btn btn-signal" disabled={busy} onClick={send}>
            <Send size={18} /> Отправить ответ
          </button>
        </div>
      </div>
    </section>
  );
}

function ClosedPanel({ app }: { app: Application }) {
  const nav = useNavigate();
  const toast = useToast();
  const reapply = useMutation({
    mutationFn: () => api<Application>(`/api/v1/applications/${app.id}/reapply`, { method: "POST" }),
    onSuccess: (a) => nav(`/c/applications/${a.id}`),
    onError: (e) => toast.error(e),
  });
  return (
    <div className="stack">
      {app.status === "DECLINED" && <DecisionExplain app={app} />}
      <section className="panel">
        <h2>{app.status === "DECLINED" ? "Что можно сделать" : "Заявка закрыта"}</h2>
        <p className="muted" style={{ marginTop: 6 }}>
          {app.status === "DECLINED"
            ? "Можно подать новую заявку на меньшую сумму, с обеспечением или после того, как изменятся обстоятельства, указанные выше."
            : app.status_view.next}
        </p>
        {app.permissions.can_reapply && (
          <button className="btn btn-primary" style={{ marginTop: 14 }} disabled={reapply.isPending} onClick={() => reapply.mutate()}>
            <RotateCcw size={18} /> Подать новую заявку с этими данными
          </button>
        )}
      </section>
    </div>
  );
}

function Messages({ app }: { app: Application }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [text, setText] = useState("");
  const msgs = useQuery({
    queryKey: ["messages", app.id],
    queryFn: () => api<Message[]>(`/api/v1/applications/${app.id}/messages`),
    refetchInterval: 6000,
  });
  const send = useMutation({
    mutationFn: () => api(`/api/v1/applications/${app.id}/messages`, { method: "POST", json: { text } }),
    onSuccess: () => {
      setText("");
      void qc.invalidateQueries({ queryKey: ["messages", app.id] });
    },
    onError: (e) => toast.error(e),
  });
  if (app.status === "DRAFT") return null;
  return (
    <section className="panel">
      <div className="panel-head">
        <h3>Переписка с банком</h3>
        <MessageSquareText size={18} className="faint" />
      </div>
      <div className="chat">
        {(msgs.data ?? []).length === 0 && <p className="small faint">Вопрос по заявке можно задать здесь — без звонков и почты.</p>}
        {msgs.data?.map((m) => (
          <div key={m.id} className={`bubble ${m.author_role === "CLIENT" ? "mine" : ""}`}>
            {m.author_role !== "CLIENT" && <b className="xs">{m.author_name}, банк</b>}
            <div>{m.text}</div>
            <time>{dateTime(m.created_at)}</time>
          </div>
        ))}
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        <input
          className="input"
          placeholder="Сообщение специалисту"
          maxLength={4000}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && text.trim() && send.mutate()}
        />
        <button className="btn btn-primary" disabled={!text.trim() || send.isPending} onClick={() => send.mutate()} aria-label="Отправить">
          <Send size={17} />
        </button>
      </div>
    </section>
  );
}

function Params({ app }: { app: Application }) {
  const { dict } = useAuth();
  return (
    <section className="panel">
      <h3>Параметры заявки</h3>
      <dl className="kv">
        <dt>Продукт</dt>
        <dd>{app.product_name}</dd>
        <dt>Цель</dt>
        <dd>{dict?.purposes.find((p) => p.code === app.purpose)?.label ?? app.purpose}</dd>
        {app.purpose_details && (
          <>
            <dt>Задача</dt>
            <dd>{app.purpose_details}</dd>
          </>
        )}
        <dt>Выручка за год</dt>
        <dd>{money(app.declared_revenue)}</dd>
        {app.has_collateral && app.collateral_description && (
          <>
            <dt>Залог</dt>
            <dd>{app.collateral_description}</dd>
          </>
        )}
        <dt>Поручительство</dt>
        <dd>{app.owner_guarantee ? "собственника бизнеса" : "нет"}</dd>
        <dt>Документы</dt>
        <dd>{app.documents.length ? app.documents.map((d) => d.doc_type_label).join(", ") : "не прикладывались"}</dd>
      </dl>
    </section>
  );
}

export function ApplicationPage() {
  const { id } = useParams<{ id: string }>();
  const toast = useToast();
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const q = useQuery({
    queryKey: ["application", id],
    queryFn: () => api<Application>(`/api/v1/applications/${id}`),
    refetchInterval: (query) => (query.state.data && PROCESSING.has(query.state.data.status) ? 1200 : 8000),
  });
  const refresh = useRefreshApp(id!);
  const lastStatus = useRef<string | null>(null);
  const status = q.data?.status;
  useEffect(() => {
    // при смене статуса возвращаем пользователя к конвейеру, чтобы он увидел, что произошло
    if (status && lastStatus.current && lastStatus.current !== status) window.scrollTo({ top: 0, behavior: "smooth" });
    lastStatus.current = status ?? null;
  }, [status]);

  if (q.isLoading) return <Loader />;
  if (q.error || !q.data) {
    return (
      <div className="panel">
        <h2>Заявка не найдена</h2>
        <p className="muted">Возможно, она принадлежит другой компании. <Link to="/c">Вернуться на главную</Link></p>
      </div>
    );
  }
  const app = q.data;
  const s = app.status;

  const cancel = async () => {
    setCancelling(true);
    try {
      await api(`/api/v1/applications/${app.id}/cancel`, { method: "POST" });
      setConfirmCancel(false);
      refresh();
    } catch (e) {
      toast.error(e);
    } finally {
      setCancelling(false);
    }
  };

  return (
    <>
      <div className="crumbs">
        <Link to="/c">Главная</Link>
        <span>/</span>
        <span>Заявка № {app.number}</span>
      </div>
      <Conveyor
        number={app.number}
        title={app.product_name}
        amount={app.amount}
        term={app.term_months}
        status={s}
        view={app.status_view}
        detail={app.stage_detail}
        aside={<StatusChip view={app.status_view} />}
      />

      <div className="grid-2" style={{ marginTop: 22 }}>
        <div className="stack">
          {s === "DRAFT" && <DraftEditor app={app} />}
          {["SUBMITTED", "PRECHECK", "ENRICHMENT", "SCORING", "DECISION"].includes(s) && <ProcessingPanel app={app} />}
          {s === "MANUAL_REVIEW" && <ReviewPanel app={app} />}
          {s === "NEEDS_INFO" && app.info_request && <InfoRequestPanel app={app} />}
          {s === "OFFER_READY" && <OfferChoice app={app} />}
          {s === "SIGNING" && <SigningPanel app={app} />}
          {(s === "DISBURSEMENT" || s === "ISSUED") && <IssuedPanel app={app} />}
          {["DECLINED", "EXPIRED", "CANCELLED", "CLIENT_DECLINED"].includes(s) && <ClosedPanel app={app} />}
          {app.permissions.can_cancel && s !== "DRAFT" && (
            <div>
              <button className="btn-link small" style={{ color: "var(--ink-2)" }} onClick={() => setConfirmCancel(true)}>
                <Undo2 size={14} style={{ verticalAlign: -2 }} /> Отозвать заявку
              </button>
            </div>
          )}
        </div>
        <aside className="stack">
          {s === "DRAFT" && <DraftEstimate app={app} />}
          {s !== "DRAFT" && <Params app={app} />}
          <Messages app={app} />
          <section className="panel">
            <h3>История</h3>
            <div style={{ marginTop: 12 }}>
              <Timeline events={app.events} />
            </div>
          </section>
          {s === "DRAFT" && app.permissions.can_cancel && (
            <div>
              <button className="btn-link small" style={{ color: "var(--ink-2)" }} onClick={() => setConfirmCancel(true)}>
                Отменить черновик
              </button>
            </div>
          )}
        </aside>
      </div>
      {confirmCancel && (
        <Modal
          title={s === "DRAFT" ? "Отменить черновик?" : "Отозвать заявку?"}
          onClose={() => setConfirmCancel(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setConfirmCancel(false)}>
                Оставить
              </button>
              <button className="btn btn-danger" disabled={cancelling} onClick={cancel}>
                {s === "DRAFT" ? "Отменить черновик" : "Отозвать заявку"}
              </button>
            </>
          }
        >
          <p>
            {s === "DRAFT"
              ? "Черновик закроется — в банк он не отправлялся. Позже можно подать новую заявку с этими данными."
              : "Банк прекратит рассмотрение. Позже можно подать новую заявку — данные заполнятся автоматически."}
          </p>
        </Modal>
      )}
    </>
  );
}
