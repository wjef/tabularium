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

async function send(method: string, path: string, body?: unknown): Promise<Response> {
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
  return response;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  return (await send(method, path, body)).json() as Promise<T>;
}

/** POST that answers with a file (e.g. an export zip): saves it with the server's file name. */
async function download(path: string, body: unknown): Promise<void> {
  const response = await send("POST", path, body);
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const name = /filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1] ?? /filename="([^"]+)"/i.exec(disposition)?.[1];
  const url = URL.createObjectURL(await response.blob());
  const link = Object.assign(document.createElement("a"), { href: url, download: name ? decodeURIComponent(name) : "export.zip" });
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  put: <T>(path: string) => request<T>("PUT", path),
  delete: <T>(path: string) => request<T>("DELETE", path),
  download,
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
  `${BASE}/documents/${id}/file${download ? "?download=1" : ""}`;

/** Small WebP preview of a PDF's first page or a photo (404 for other types). */
export const thumbnailUrl = (id: string) => `${BASE}/documents/${id}/thumbnail`;

// --- management endpoints that return plain JSON (not described in detail by OpenAPI) ---

export interface EntityRef {
  id: string;
  name: string;
  type_name: string;
}

export interface EntityDetail {
  id: string;
  name: string;
  type_id: string;
  type_name: string;
  aliases: string[];
  attributes: Record<string, unknown>;
  tag_ids: string[];
  groups: EntityRef[];
  indirect_groups: EntityRef[];
  members: EntityRef[];
  indirect_members: EntityRef[];
  history: HistoryEntry[];
}

export type EntityType = Schemas["EntityTypeOut"];

export interface OutdatedSummary {
  id: string;
  display_name: string;
  document_date: string | null;
  outdated_at: string;
  outdated_by: string;
  reason: string | null;
  tag_paths: string[];
  mime_type: string | null;
}

export interface OutdatedDetail extends OutdatedSummary {
  title: string | null;
  note: string | null;
  retain_until: string;
  stored_path: string;
  original_filename: string;
  events: { at: string; actor: string; action: string; data: unknown }[];
}

export interface MaintenanceStatus {
  text_counts: Record<string, number>;
  ocr: { enabled: boolean; available: boolean; command: string | null; languages: string; message: string };
  metadata: { file: string; exists: boolean; last_written: string | null; last_error: string | null };
  backups: { dir: string; keep: number; files: { name: string; size: number }[] };
  can_restore: boolean;
  outdated_restore_pending: boolean;
}

/** Result of scanning the ingest folder: file names per outcome. */
export interface IngestReport {
  imported: string[];
  duplicates: string[];
  failed: string[];
  skipped: string[];
  waiting: string[];
}

export interface CheckProblem {
  path: string;
  document_id: string | null;
  name: string | null;
  detail: string;
}

export interface CheckReport {
  full: boolean;
  checked: number;
  ok: number;
  healthy: boolean;
  missing: CheckProblem[];
  changed: CheckProblem[];
  cloud_only: CheckProblem[];
  orphans: CheckProblem[];
}

/** URL of an outdated document's file, for preview. */
export const outdatedFileUrl = (id: string) => `${BASE}/outdated/${id}/file`;
