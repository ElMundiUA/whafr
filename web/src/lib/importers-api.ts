// Server-side client for the Python /admin/importers router. The
// public web app never hits this directly — only the Astro API
// routes under /api/admin/importers/ do, and they're guarded by the
// same admin redirect every other /admin page uses.
//
// The admin token (LIGHTHOUSE_ADMIN_TOKEN) is optional: if the Python
// API has it set, every request needs the Bearer header; we forward
// it from server-side env so the browser never sees it.

import { API_BASE } from "@/lib/api-base";

export interface ImporterType {
  type: string;
  display_name: string;
  description: string;
  config_schema: Record<string, unknown>;
  secret_keys: string[];
  supports_discovery: boolean;
  discovery_required: string[];
}

export interface DiscoveredItem {
  id: string;
  name: string;
  kind: string;
  hint: string | null;
  config_patch: Record<string, unknown>;
}

export interface Importer {
  id: string;
  type: string;
  name: string;
  description: string | null;
  recipe: string;
  config: Record<string, unknown>;
  has_secrets: boolean;
  enabled: boolean;
  status: "idle" | "queued" | "running" | "error";
  last_run_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface ImporterRun {
  id: string;
  importer_id: string;
  started_at: string;
  finished_at: string | null;
  status: "running" | "success" | "error" | "cancelled";
  items_total: number | null;
  items_done: number;
  chunks_added: number;
  error_text: string | null;
  triggered_by: string | null;
}

function adminHeaders(): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  const tok = process.env.LIGHTHOUSE_ADMIN_TOKEN;
  if (tok) h["Authorization"] = `Bearer ${tok}`;
  return h;
}

async function call<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const r = await fetch(`${API_BASE}/admin/importers${path}`, {
    ...init,
    headers: { ...adminHeaders(), ...(init?.headers || {}) },
  });
  if (!r.ok) {
    const body = await r.text();
    throw new Error(`Python API ${r.status}: ${body.slice(0, 500)}`);
  }
  if (r.status === 204) return undefined as T;
  return (await r.json()) as T;
}

// ────── reads ──────

export const listTypes = (): Promise<ImporterType[]> => call("/types");

export const listImporters = (): Promise<Importer[]> => call("/");

export const getImporter = (id: string): Promise<Importer> => call(`/${id}`);

export const listRuns = (id: string): Promise<ImporterRun[]> => call(`/${id}/runs`);

// ────── writes ──────

export const createImporter = (body: {
  type: string;
  name: string;
  description?: string | null;
  recipe: string;
  config: Record<string, unknown>;
  secrets?: Record<string, string>;
}): Promise<Importer> =>
  call("/", { method: "POST", body: JSON.stringify(body) });

export const patchImporter = (
  id: string,
  body: Partial<{
    name: string;
    description: string | null;
    recipe: string;
    config: Record<string, unknown>;
    secrets: Record<string, string>;
    enabled: boolean;
  }>,
): Promise<Importer> =>
  call(`/${id}`, { method: "PATCH", body: JSON.stringify(body) });

export const deleteImporter = (id: string): Promise<void> =>
  call(`/${id}`, { method: "DELETE" });

export const runImporter = (
  id: string,
): Promise<{ run_id: string; importer_id: string; status: string }> =>
  call(`/${id}/run`, { method: "POST" });

export const discover = (body: {
  type: string;
  config: Record<string, unknown>;
  secrets: Record<string, string>;
}): Promise<{ items: DiscoveredItem[] }> =>
  call("/discover", { method: "POST", body: JSON.stringify(body) });
