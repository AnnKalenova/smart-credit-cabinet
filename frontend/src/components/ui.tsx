import { Check, X } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import type { AppEvent, StatusView, TurnoverPoint } from "../api/types";
import { dateTime, money, monthShort, moneyShort } from "../lib/format";

export const PROCESSING = new Set(["SUBMITTED", "PRECHECK", "ENRICHMENT", "SCORING", "DECISION", "MANUAL_REVIEW", "DISBURSEMENT"]);

export function StatusChip({ view, label }: { view: Pick<StatusView, "tone" | "title">; label?: string }) {
  return <span className={`chip ${view.tone}`}>{label ?? view.title}</span>;
}

export function MiniTrack({ status, view }: { status: string; view: StatusView }) {
  return (
    <div className="mini-track" aria-hidden>
      {view.steps.map((_, i) => {
        let cls = "";
        if (status === "ISSUED" || i < view.step) cls = "done";
        else if (i === view.step) cls = view.tone === "negative" ? "failed" : "current";
        return <i key={i} className={cls} />;
      })}
    </div>
  );
}

export function Conveyor({
  number,
  title,
  amount,
  term,
  status,
  view,
  detail,
  aside,
}: {
  number: string;
  title: string;
  amount: number;
  term: number;
  status: string;
  view: StatusView;
  detail: string | null;
  aside?: ReactNode;
}) {
  const issued = status === "ISSUED";
  const working = PROCESSING.has(status) && status !== "MANUAL_REVIEW";
  const fill = Math.min(view.step, 4) * 20;
  return (
    <section className="conveyor" aria-label="Ход рассмотрения заявки">
      <div className="conveyor-top">
        <div>
          <div className="number">
            Заявка № {number}, {title}
          </div>
          <div className="amount">
            {money(amount)}
            <small>на {term} мес.</small>
          </div>
        </div>
        {aside}
      </div>
      <div className="track">
        <div className="track-fill" style={{ width: `${fill}%` }} />
        {view.steps.map((s, i) => {
          let cls = "station";
          if (issued || i < view.step) cls += " done";
          else if (i === view.step) cls += view.tone === "negative" ? " failed" : " current";
          if (i === view.step && (working || status === "MANUAL_REVIEW")) cls += " working";
          const icon = cls.includes("done") ? <Check size={15} strokeWidth={3} /> : cls.includes("failed") ? <X size={15} strokeWidth={3} /> : null;
          return (
            <div key={s} className={cls}>
              <span className="dot">{icon}</span>
              <span className="label">{s}</span>
            </div>
          );
        })}
      </div>
      <div className="now-panel">
        <div>
          <div className="label">Сейчас</div>
          <div>
            <b>{view.title}.</b> {view.now}
          </div>
          {detail && !view.is_final && (
            <div className="detail">
              {working && <span className="spin" aria-hidden />}
              {detail}
            </div>
          )}
        </div>
        <div>
          <div className="label">Что дальше</div>
          <div>{view.next}</div>
        </div>
      </div>
    </section>
  );
}

export function Modal({
  title,
  onClose,
  children,
  footer,
  wide,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="icon-btn" style={{ color: "var(--ink-2)" }} onClick={onClose} aria-label="Закрыть">
            <X size={18} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children}
    </div>
  );
}

export function Loader({ text = "Загружаем…" }: { text?: string }) {
  return (
    <div className="empty row" style={{ justifyContent: "center" }}>
      <span className="spin" /> {text}
    </div>
  );
}

export function Grade({ grade }: { grade: string | null | undefined }) {
  if (!grade) return <span className="faint">—</span>;
  return (
    <span className={`grade grade-${grade}`} title={`Рейтинг ${grade}`}>
      {grade}
    </span>
  );
}

export function Timeline({ events, showInternal = false }: { events: AppEvent[]; showInternal?: boolean }) {
  const list = showInternal ? events : events.filter((e) => e.client_visible !== false);
  return (
    <ol className="timeline">
      {list.map((e) => (
        <li key={e.id} className={e.client_visible === false ? "internal" : ""}>
          <time>
            {dateTime(e.created_at)}
            {e.actor_name ? `, ${e.actor_name}` : e.actor_type === "SYSTEM" ? ", система" : ""}
          </time>
          {e.title}
        </li>
      ))}
    </ol>
  );
}

export function TurnoverBars({ series }: { series: TurnoverPoint[] }) {
  if (!series.length) return <p className="muted small">Нет данных об оборотах</p>;
  const max = Math.max(...series.map((s) => s.credit));
  return (
    <div>
      <div className="bars" role="img" aria-label="Поступления на расчётный счёт по месяцам">
        {series.map((s, i) => (
          <div
            key={s.month}
            className={i === series.length - 1 ? "last" : ""}
            style={{ height: `${(s.credit / max) * 100}%` }}
            title={`${monthShort(s.month)}: ${moneyShort(s.credit)}`}
          />
        ))}
      </div>
      <div className="bars-labels">
        {series.map((s) => (
          <span key={s.month}>{monthShort(s.month)}</span>
        ))}
      </div>
    </div>
  );
}

export function Field({ label, hint, error, children }: { label: string; hint?: string; error?: string; children: ReactNode }) {
  // label оборачивает поле — так подпись связана с полем для скринридеров и автотестов
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {error ? <span className="field-error">{error}</span> : hint ? <span className="field-hint">{hint}</span> : null}
    </label>
  );
}

export function ProblemList({ problems }: { problems: string[] }) {
  if (!problems.length) return null;
  return (
    <div className="callout bad" role="alert">
      <div>
        <b>Чтобы продолжить:</b>
        <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}
