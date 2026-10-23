const rub = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });
const rub2 = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const NBSP = "\u00a0";

/** Сумма: разряды и знак рубля — через неразрывный пробел, чтобы строка не переносилась внутри числа. */
export function money(v: number | null | undefined, cents = false): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${(cents ? rub2 : rub).format(v)}${NBSP}₽`;
}

export function moneyShort(v: number): string {
  if (Math.abs(v) >= 1_000_000) {
    const m = v / 1_000_000;
    return `${m.toLocaleString("ru-RU", { maximumFractionDigits: m >= 10 ? 1 : 2 })}${NBSP}млн${NBSP}₽`;
  }
  if (Math.abs(v) >= 1_000) return `${Math.round(v / 1_000).toLocaleString("ru-RU")}${NBSP}тыс.${NBSP}₽`;
  return `${Math.round(v)}${NBSP}₽`;
}

/** Проценты без лишнего нуля: «22%», «21,5%». */
export function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return "—";
  return `${v.toLocaleString("ru-RU", { minimumFractionDigits: 0, maximumFractionDigits: digits })}%`;
}

/** Диапазон ставок одним видом везде: «20,5–23%». */
export function rateRange(from: number, to: number): string {
  if (Math.abs(from - to) < 0.05) return pct(from);
  const f = (v: number) => v.toLocaleString("ru-RU", { maximumFractionDigits: 1 });
  return `${f(from)}–${f(to)}%`;
}

export function date(v: string | null | undefined): string {
  if (!v) return "—";
  return new Date(v).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
}

export function dateTime(v: string | null | undefined): string {
  if (!v) return "—";
  return new Date(v).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function time(v: string): string {
  return new Date(v).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

export function months(n: number): string {
  return `${n}${NBSP}${plural(n, "месяц", "месяца", "месяцев")}`;
}

// трёхбуквенные подписи месяцев: помещаются под столбиками графика и на узком экране
const MONTHS_SHORT = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

export function monthShort(ym: string): string {
  const m = Number(ym.split("-")[1]);
  return MONTHS_SHORT[m - 1] ?? ym;
}

export function relativeDue(v: string | null): { text: string; overdue: boolean } {
  if (!v) return { text: "—", overdue: false };
  const diff = new Date(v).getTime() - Date.now();
  const mins = Math.round(Math.abs(diff) / 60000);
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  const span = h > 0 ? `${h}${NBSP}ч ${m}${NBSP}мин` : `${m}${NBSP}мин`;
  return diff >= 0 ? { text: `осталось ${span}`, overdue: false } : { text: `просрочено на ${span}`, overdue: true };
}

export function fileSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toLocaleString("ru-RU", { maximumFractionDigits: 1 })}${NBSP}МБ`;
  return `${Math.max(1, Math.round(bytes / 1024))}${NBSP}КБ`;
}

export function parseAmount(s: string): number {
  return Number(s.replace(/\s/g, "").replace(",", ".")) || 0;
}

/** Цифры по три через неразрывный пробел: «1000000» → «1 000 000». Ведущие нули сохраняются —
 *  пока человек редактирует поле, стёртая первая цифра не уничтожает остальные. */
export function groupDigits(digits: string): string {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
}

export function formatInputAmount(v: number | string): string {
  const n = typeof v === "number" ? v : parseAmount(v);
  if (!n) return "";
  return rub.format(n);
}
