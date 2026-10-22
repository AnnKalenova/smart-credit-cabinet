const rub = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });
const rub2 = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function money(v: number | null | undefined, cents = false): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${(cents ? rub2 : rub).format(v)} ₽`;
}

export function moneyShort(v: number): string {
  if (Math.abs(v) >= 1_000_000) {
    const m = v / 1_000_000;
    return `${m.toLocaleString("ru-RU", { maximumFractionDigits: m >= 10 ? 1 : 2 })} млн ₽`;
  }
  if (Math.abs(v) >= 1_000) return `${Math.round(v / 1_000).toLocaleString("ru-RU")} тыс. ₽`;
  return `${Math.round(v)} ₽`;
}

export function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return "—";
  return `${v.toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`;
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
  return `${n} ${plural(n, "месяц", "месяца", "месяцев")}`;
}

export function monthShort(ym: string): string {
  const [y, m] = ym.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("ru-RU", { month: "short" }).replace(".", "");
}

export function relativeDue(v: string | null): { text: string; overdue: boolean } {
  if (!v) return { text: "—", overdue: false };
  const diff = new Date(v).getTime() - Date.now();
  const mins = Math.round(Math.abs(diff) / 60000);
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  const span = h > 0 ? `${h} ч ${m} мин` : `${m} мин`;
  return diff >= 0 ? { text: `осталось ${span}`, overdue: false } : { text: `просрочено на ${span}`, overdue: true };
}

export function fileSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} МБ`;
  return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
}

export function parseAmount(s: string): number {
  return Number(s.replace(/\s/g, "").replace(",", ".")) || 0;
}

export function formatInputAmount(v: number | string): string {
  const n = typeof v === "number" ? v : parseAmount(v);
  if (!n) return "";
  return rub.format(n);
}
