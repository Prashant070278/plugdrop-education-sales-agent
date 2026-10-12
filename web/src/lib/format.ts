export function label(v: string | null | undefined): string {
  if (!v) return "—";
  const special: Record<string, string> = {
    voice_ai: "Voice AI", crm: "CRM", edtech: "EdTech", "1k_5k": "1k–5k", "5k_20k": "5k–20k", "20k_50k": "20k–50k",
    under_1k: "Under 1k", over_50k: "50k+", test_prep: "Test prep", whatsapp: "WhatsApp", demo: "Product demo",
  };
  if (special[v]) return special[v];
  const s = v.replaceAll("_", " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function money(v: string | number | null | undefined, currency = "USD"): string {
  const n = typeof v === "string" ? Number(v) : v ?? 0;
  return new Intl.NumberFormat("en-US", { style: "currency", currency, maximumFractionDigits: 0 }).format(n || 0);
}

export function compactMoney(v: string | number): string {
  const n = typeof v === "string" ? Number(v) : v;
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 }).format(n || 0);
}

export function pct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `${(v * 100).toFixed(v < 0.1 && v > 0 ? 1 : 0)}%`;
}

export function date(v: string | null | undefined): string {
  if (!v) return "—";
  const d = new Date(v.length === 10 ? `${v}T00:00:00` : v);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function dateTime(v: string | null | undefined): string {
  if (!v) return "—";
  return new Date(v).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function relative(v: string | null | undefined): string {
  if (!v) return "—";
  const diff = (new Date(v).getTime() - Date.now()) / 1000;
  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  const abs = Math.abs(diff);
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  if (abs < 86400 * 30) return rtf.format(Math.round(diff / 86400), "day");
  return date(v);
}

/** ISO string -> value for <input type="datetime-local"> in local time. */
export function toLocalInput(v: string | null | undefined): string {
  if (!v) return "";
  const d = new Date(v);
  const off = d.getTimezoneOffset();
  return new Date(d.getTime() - off * 60000).toISOString().slice(0, 16);
}

export function fromLocalInput(v: string): string | null {
  return v ? new Date(v).toISOString() : null;
}
