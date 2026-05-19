// Lighthouse engine client — TypeScript.
//
// Pairs the auto-generated `openapi.d.ts` types with a tiny fetch
// wrapper. Designed for use from server-side Node (Ship's API layer)
// and from edge runtimes; no Node-only imports.
//
// Re-run `pnpm regen` after every Lighthouse API change to refresh
// `openapi.d.ts` from /openapi.json.

import type { paths, components } from "./openapi";

// ────────────────────────── Helpers ──────────────────────────

export type LighthouseConfig = {
  /** Base URL of the engine, e.g. `https://lighthouse.example.com`. */
  baseUrl: string;
  /** Optional bearer token — required when the engine deployment
   *  has LIGHTHOUSE_ADMIN_TOKEN set. */
  token?: string;
  /** Optional fetch override (handy for tests / custom retry layers). */
  fetch?: typeof fetch;
};

export type Importer = components["schemas"]["ImporterOut"];
export type ImporterCreate = components["schemas"]["ImporterCreate"];
export type ImporterUpdate = components["schemas"]["ImporterUpdate"];
export type ImporterType = components["schemas"]["ImporterTypeOut"];
export type ImporterRun = components["schemas"]["RunOut"];
export type DiscoveredItem = components["schemas"]["DiscoveredItemOut"];
export type CorpusStats = components["schemas"]["CorpusStats"];
export type CorpusSource = components["schemas"]["CorpusSource"];
export type Webhook = components["schemas"]["WebhookOut"];
export type WebhookCreated = components["schemas"]["WebhookCreated"];
export type Delivery = components["schemas"]["DeliveryOut"];

export class LighthouseError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: string,
    message?: string,
  ) {
    super(message || `Lighthouse ${status}: ${body.slice(0, 200)}`);
  }
}

// ────────────────────────── Client ───────────────────────────

export function createClient(cfg: LighthouseConfig) {
  const fetchImpl = cfg.fetch ?? fetch;
  const base = cfg.baseUrl.replace(/\/$/, "");

  async function call<T>(
    path: string,
    init: RequestInit = {},
  ): Promise<T> {
    const headers: Record<string, string> = {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers as Record<string, string> | undefined),
    };
    if (cfg.token) headers["Authorization"] = `Bearer ${cfg.token}`;
    const r = await fetchImpl(`${base}${path}`, { ...init, headers });
    if (!r.ok) {
      throw new LighthouseError(r.status, await r.text());
    }
    if (r.status === 204) return undefined as T;
    return (await r.json()) as T;
  }

  return {
    // ────── retrieval ──────
    search: (q: string, opts: { top_k?: number; sort?: "relevance" | "newest" } = {}) =>
      call<paths["/v1/search"]["get"]["responses"]["200"]["content"]["application/json"]>(
        `/v1/search?${new URLSearchParams({
          q,
          ...(opts.top_k ? { top_k: String(opts.top_k) } : {}),
          ...(opts.sort ? { sort: opts.sort } : {}),
        })}`,
      ),
    fetchEntity: (nodeId: string) =>
      call<components["schemas"]["EntityResponse"]>(
        `/v1/fetch_entity/${encodeURIComponent(nodeId)}`,
      ),
    fetchSource: (episodeId: string) =>
      call<components["schemas"]["SourceResponse"]>(
        `/v1/fetch_source/${encodeURIComponent(episodeId)}`,
      ),

    // ────── corpus ──────
    corpus: {
      stats: () => call<CorpusStats>("/v1/corpus/stats"),
      sources: (opts: { limit?: number; order?: "chunks" | "recent" } = {}) =>
        call<CorpusSource[]>(
          `/v1/corpus/sources?${new URLSearchParams({
            ...(opts.limit ? { limit: String(opts.limit) } : {}),
            ...(opts.order ? { order: opts.order } : {}),
          })}`,
        ),
    },

    // ────── importers ──────
    importers: {
      listTypes: () => call<ImporterType[]>("/v1/importers/types"),
      list: () => call<Importer[]>("/v1/importers/"),
      get: (id: string) => call<Importer>(`/v1/importers/${id}`),
      create: (body: ImporterCreate) =>
        call<Importer>("/v1/importers/", { method: "POST", body: JSON.stringify(body) }),
      update: (id: string, body: ImporterUpdate) =>
        call<Importer>(`/v1/importers/${id}`, {
          method: "PATCH",
          body: JSON.stringify(body),
        }),
      delete: (id: string) =>
        call<void>(`/v1/importers/${id}`, { method: "DELETE" }),
      run: (id: string) =>
        call<{ run_id: string; importer_id: string; status: string }>(
          `/v1/importers/${id}/run`,
          { method: "POST" },
        ),
      runs: (id: string) => call<ImporterRun[]>(`/v1/importers/${id}/runs`),
      discover: (body: { type: string; config: Record<string, unknown>; secrets: Record<string, string> }) =>
        call<{ items: DiscoveredItem[] }>("/v1/importers/discover", {
          method: "POST",
          body: JSON.stringify(body),
        }),
    },

    // ────── webhooks ──────
    webhooks: {
      list: () => call<Webhook[]>("/v1/webhooks/"),
      get: (id: string) => call<Webhook>(`/v1/webhooks/${id}`),
      create: (body: {
        url: string;
        events?: string[];
        description?: string | null;
        secret?: string | null;
        enabled?: boolean;
      }) =>
        call<WebhookCreated>("/v1/webhooks/", {
          method: "POST",
          body: JSON.stringify(body),
        }),
      update: (
        id: string,
        body: {
          url?: string;
          events?: string[];
          description?: string | null;
          enabled?: boolean;
          rotate_secret?: boolean;
        },
      ) =>
        call<Webhook | WebhookCreated>(`/v1/webhooks/${id}`, {
          method: "PATCH",
          body: JSON.stringify(body),
        }),
      delete: (id: string) =>
        call<void>(`/v1/webhooks/${id}`, { method: "DELETE" }),
      deliveries: (id: string, opts: { limit?: number } = {}) =>
        call<Delivery[]>(
          `/v1/webhooks/${id}/deliveries${
            opts.limit ? `?limit=${opts.limit}` : ""
          }`,
        ),
      redeliver: (id: string, deliveryId: string) =>
        call<{ status: string }>(
          `/v1/webhooks/${id}/deliveries/${deliveryId}/redeliver`,
          { method: "POST" },
        ),
      test: (id: string) =>
        call<{ delivery_id: string }>(`/v1/webhooks/${id}/test`, {
          method: "POST",
        }),
    },
  };
}

export type LighthouseClient = ReturnType<typeof createClient>;

// ────────────────────────── HMAC verify ──────────────────────────

/**
 * Verify a Lighthouse webhook signature on the receiver side.
 * Pass the raw request body bytes (not parsed JSON) and the value of
 * the `X-Lighthouse-Signature` header.
 *
 * Returns true iff the HMAC-SHA256 over `body` under `secret` matches.
 * Uses Web Crypto, so it runs on Node 18+, Bun, Deno, edge runtimes.
 */
export async function verifyWebhookSignature(
  secret: string,
  body: Uint8Array | string,
  signatureHeader: string,
): Promise<boolean> {
  if (!signatureHeader || !signatureHeader.startsWith("sha256=")) return false;
  const expected = signatureHeader.slice("sha256=".length);
  const bodyBytes = typeof body === "string" ? new TextEncoder().encode(body) : body;
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, bodyBytes);
  const hex = [...new Uint8Array(sig)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  // Constant-time compare
  if (hex.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < hex.length; i++) {
    diff |= hex.charCodeAt(i) ^ expected.charCodeAt(i);
  }
  return diff === 0;
}
