import useSWR, { type SWRConfiguration } from "swr";

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

type Query = Record<string, string | number | boolean | null | undefined | (string | number)[]>;

export function qs(params?: Query): string {
  if (!params) return "";
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => sp.append(k, String(x)));
    else sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

function messageFrom(body: unknown, fallback: string): string {
  if (body && typeof body === "object") {
    const b = body as { detail?: unknown; errors?: { field: string; message: string }[] };
    if (Array.isArray(b.errors) && b.errors.length) {
      return b.errors.map((e) => (e.field ? `${e.field.replaceAll("_", " ")}: ${e.message}` : e.message)).join(" · ");
    }
    if (typeof b.detail === "string") return b.detail;
    if (b.detail && typeof b.detail === "object" && "message" in b.detail) {
      const d = b.detail as { message: string; duplicates?: { label: string; reason: string }[] };
      const dups = d.duplicates?.map((x) => `${x.label} (${x.reason})`).join(", ");
      return dups ? `${d.message}: ${dups}` : d.message;
    }
  }
  return fallback;
}

export async function api<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const headers = new Headers(init?.headers);
  let body = init?.body;
  if (init?.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  const res = await fetch(`/api${path}`, { ...init, body, headers, credentials: "same-origin" });
  if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/auth/")) {
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  const data = text ? (() => { try { return JSON.parse(text); } catch { return text; } })() : undefined;
  if (!res.ok) throw new ApiError(res.status, messageFrom(data, `Request failed (${res.status})`), data);
  return data as T;
}

export function useApi<T>(path: string | null, params?: Query, config?: SWRConfiguration<T>) {
  const key = path ? `${path}${qs(params)}` : null;
  return useSWR<T>(key, (k: string) => api<T>(k), { revalidateOnFocus: false, keepPreviousData: true, ...config });
}
