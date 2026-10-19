// Typed access to the Tabularium API (/api/v1). Types come from schema.d.ts, which is
// generated from the backend's OpenAPI description (`npm run gen:api`).
import type { components } from "./schema";

export type Schemas = components["schemas"];
export type DocumentSummary = Schemas["DocumentSummary"];
export type DocumentDetail = Schemas["DocumentDetail"];
export type DocumentPage = Schemas["DocumentPage"];
export type TagNode = Schemas["TagNode"];
export type TagRef = Schemas["TagRef"];
export type EntityOut = Schemas["EntityOut"];
export type LinkOut = Schemas["LinkOut"];
export type HistoryEntry = Schemas["HistoryEntry"];

export interface AppInfo {
  version: string;
  language: string;
  untagged: number;
  outdated: number;
  restore_pending: boolean;
}

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const BASE = "/api/v1";
let csrf: Promise<{ csrf_header: string; csrf_token: string }> | null = null;

function session() {
  csrf ??= fetch(`${BASE}/session`).then((r) => r.json());
  return csrf;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (method !== "GET") {
    const { csrf_header, csrf_token } = await session();
    headers[csrf_header] = csrf_token;
  }
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let message = response.statusText;
    try {
      const data = await response.json();
      if (typeof data.detail === "string") message = data.detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  put: <T>(path: string) => request<T>("PUT", path),
  delete: <T>(path: string) => request<T>("DELETE", path),
};

/** Query string from a params object; arrays become repeated keys, empty values are skipped. */
export function qs(params: Record<string, string | number | boolean | string[] | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "" || value === false) continue;
    if (Array.isArray(value)) value.forEach((v) => search.append(key, v));
    else search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

/** URL of a document's file (preview inline or as a download). */
export const fileUrl = (id: string, download = false) =>
  `/documents/${id}/file${download ? "?download=1" : ""}`;

/** Small WebP preview of a PDF's first page or a photo (404 for other types). */
export const thumbnailUrl = (id: string) => `${BASE}/documents/${id}/thumbnail`;
