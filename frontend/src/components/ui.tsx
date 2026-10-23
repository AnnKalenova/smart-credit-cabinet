import { Check, X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, type InputHTMLAttributes, type ReactNode } from "react";
import type { AppEvent, StatusView, TurnoverPoint } from "../api/types";
import { dateTime, formatInputAmount, groupDigits, money, monthShort, moneyShort } from "../lib/format";

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
        else if (i === view.step) cls = view.tone === "negative" ? "failed" : view.tone === "neutral" ? "stopped" : "current";
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
          else if (i === view.step) cls += view.tone === "negative" ? " failed" : view.tone === "neutral" ? " stopped" : " current";
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

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Модальное окно: фокус переходит внутрь и не уходит за подложку, Escape закрывает,
 *  после закрытия фокус возвращается туда, откуда окно открыли. */
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
  const box = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const el = box.current;
    const field = el?.querySelector<HTMLElement>("input:not([disabled]), textarea:not([disabled]), select:not([disabled])");
    (field ?? el)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        close.current();
        return;
      }
      if (e.key !== "Tab" || !el) return;
      const items = Array.from(el.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (!items.length) {
        e.preventDefault();
        el.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const inside = el.contains(document.activeElement);
      if (e.shiftKey && (!inside || document.activeElement === first || document.activeElement === el)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (!inside || document.activeElement === last)) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      if (opener && document.contains(opener)) opener.focus();
    };
  }, []);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={box} tabIndex={-1} className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
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

/** Поле суммы с разрядами. Курсор остаётся у той же цифры, когда разряды сдвигаются; если стереть первую
 *  цифру, остальные не пропадают (её можно набрать заново), лишние нули убираются при уходе из поля. */
export function AmountInput({
  value,
  onValueChange,
  maxDigits = 13,
  onBlur,
  ...rest
}: Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange"> & {
  value: string;
  onValueChange: (text: string) => void;
  maxDigits?: number;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const caret = useRef<number | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && caret.current !== null && document.activeElement === el) el.setSelectionRange(caret.current, caret.current);
    caret.current = null;
  });
  return (
    <input
      {...rest}
      ref={ref}
      inputMode="numeric"
      autoComplete="off"
      value={value}
      onChange={(e) => {
        const raw = e.target.value;
        const pos = e.target.selectionStart ?? raw.length;
        const digits = raw.replace(/\D/g, "").slice(0, maxDigits);
        const before = Math.min(raw.slice(0, pos).replace(/\D/g, "").length, digits.length);
        const text = groupDigits(digits);
        // курсор — сразу после той же по счёту цифры, что и до форматирования
        let i = 0;
        for (let seen = 0; i < text.length && seen < before; i++) if (/\d/.test(text[i])) seen++;
        if (text === value) {
          // введён не цифровой символ: значение не меняется, React вернёт прежний текст — курсор ставим сами
          requestAnimationFrame(() => document.activeElement === ref.current && ref.current?.setSelectionRange(i, i));
          return;
        }
        caret.current = i;
        onValueChange(text);
      }}
      onBlur={(e) => {
        const clean = formatInputAmount(value);
        if (clean !== value) onValueChange(clean);
        onBlur?.(e);
      }}
    />
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
