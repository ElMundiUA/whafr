/* Lighthouse admin SPA — no build step, hash router, vanilla ES modules.
 *
 * Talks to the engine's own /v1 surface. Admin token + workspace are
 * kept in localStorage and attached to every request as
 * `Authorization: Bearer …` / `X-Workspace: …`.
 */

const LS_TOKEN = "lighthouse.adminToken";
const LS_WORKSPACE = "lighthouse.workspace";

const $ = (sel, root = document) => root.querySelector(sel);
const main = $("#main");

// ───────────────────────── API helpers ─────────────────────────

function authHeaders() {
  const h = {};
  const token = localStorage.getItem(LS_TOKEN);
  const ws = localStorage.getItem(LS_WORKSPACE);
  if (token) h["Authorization"] = `Bearer ${token}`;
  if (ws) h["X-Workspace"] = ws;
  return h;
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...(opts.headers || {}),
    },
  });
  if (res.status === 204) return null;
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON error body */ }
  if (!res.ok) {
    const detail = body?.detail ?? res.statusText;
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = res.status;
    throw err;
  }
  return body;
}

// ───────────────────────── UI primitives ─────────────────────────

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined) continue;
    node.append(c.nodeType ? c : document.createTextNode(c));
  }
  return node;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove("show"), 2600);
}

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function fmtNum(n) {
  return (n ?? 0).toLocaleString();
}

function errorBox(err) {
  const hint = err.status === 401
    ? " — set the admin token via ⚙ Connection (bottom-left)."
    : "";
  return el("div", { class: "error-box" }, `Error: ${err.message}${hint}`);
}

function pill(text, kind) {
  return el("span", { class: `pill ${kind || ""}` }, el("span", { class: "dot" }), text);
}

function statusPill(imp) {
  if (!imp.enabled) return pill("Disabled", "");
  if (imp.status === "running") return pill("Running", "run");
  if (imp.status === "queued") return pill("Queued", "warn");
  if (imp.status === "error") return pill("Error", "err");
  return pill("Ready", "ok");
}

const ICON_COLORS = ["#3742fa", "#7c4dff", "#0ea5e9", "#22a55a", "#e8590c", "#d6336c", "#b97a00", "#0c8599"];
function srcIcon(type) {
  let hash = 0;
  for (const ch of type) hash = (hash * 31 + ch.charCodeAt(0)) | 0;
  const color = ICON_COLORS[Math.abs(hash) % ICON_COLORS.length];
  const label = type.replace(/[^a-z0-9]/gi, "").slice(0, 2).toUpperCase() || "?";
  return el("span", { class: "src-ico", style: `background:${color}` }, label);
}

// Horizontal bar list (kapa "Top sources" style).
function barList(rows, { label, value, max }) {
  const top = max ?? Math.max(1, ...rows.map(value));
  return el("div", {}, rows.map((r) =>
    el("div", { class: "bar-row" },
      el("span", { class: "bar-label", title: label(r) }, label(r)),
      el("span", { class: "bar-track" },
        el("span", { class: "bar-fill", style: `width:${Math.max(2, (value(r) / top) * 100)}%` })),
      el("span", { class: "bar-val" }, fmtNum(value(r))),
    )
  ));
}

// SVG area chart (kapa "Questions/week" style): questions + gaps overlay.
function areaChart(points, w = 640, h = 200) {
  const pad = { l: 36, r: 8, t: 12, b: 24 };
  const iw = w - pad.l - pad.r, ih = h - pad.t - pad.b;
  const maxY = Math.max(5, ...points.map((p) => p.questions));
  const x = (i) => pad.l + (points.length < 2 ? iw / 2 : (i / (points.length - 1)) * iw);
  const y = (v) => pad.t + ih - (v / maxY) * ih;
  const line = (key) => points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`).join(" ");
  const area = (key) =>
    `${line(key)} L${x(points.length - 1).toFixed(1)},${y(0)} L${x(0).toFixed(1)},${y(0)} Z`;

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  const put = (name, attrs) => {
    const n = document.createElementNS("http://www.w3.org/2000/svg", name);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    svg.append(n);
    return n;
  };
  for (const frac of [0, 0.5, 1]) {
    const gy = pad.t + ih - frac * ih;
    put("line", { x1: pad.l, x2: w - pad.r, y1: gy, y2: gy, stroke: "#ececef", "stroke-width": 1 });
    put("text", { x: pad.l - 8, y: gy + 4, "text-anchor": "end", "font-size": 10.5, fill: "#9a9aa2" })
      .textContent = Math.round(maxY * frac);
  }
  if (points.length) {
    put("path", { d: area("questions"), fill: "rgba(55,66,250,.14)" });
    put("path", { d: line("questions"), fill: "none", stroke: "#3742fa", "stroke-width": 2 });
    put("path", { d: area("gaps"), fill: "rgba(210,59,59,.15)" });
    put("path", { d: line("gaps"), fill: "none", stroke: "#d23b3b", "stroke-width": 1.6 });
    const first = points[0], last = points[points.length - 1];
    for (const [p, anchor, px] of [[first, "start", x(0)], [last, "end", x(points.length - 1)]]) {
      put("text", { x: px, y: h - 7, "text-anchor": anchor, "font-size": 10.5, fill: "#9a9aa2" })
        .textContent = new Date(p.day).toLocaleDateString(undefined, { month: "short", day: "numeric" });
    }
  }
  return el("div", { class: "chart-wrap" }, svg);
}

function daysFilter(current, onChange) {
  const sel = el("select", { onchange: (e) => onChange(Number(e.target.value)) },
    ...[7, 30, 90].map((d) =>
      el("option", { value: d, ...(d === current ? { selected: "" } : {}) }, `Last ${d} days`)));
  return sel;
}

function pageHead(title, ...filters) {
  return el("div", { class: "page-head" }, el("h1", {}, title),
    el("div", { class: "filters" }, ...filters));
}

// ───────────────────────── Pages ─────────────────────────

async function dashboardPage() {
  let days = 30;
  const render = async () => {
    main.replaceChildren(pageHead("Dashboard", daysFilter(days, (d) => { days = d; render(); })));
    try {
      const [stats, overview, usage, gaps] = await Promise.all([
        api("/v1/corpus/stats"),
        api(`/v1/analytics/overview?days=${days}`),
        api(`/v1/analytics/source-usage?days=${days}&limit=6`),
        api(`/v1/analytics/gaps?days=${days}&limit=6`),
      ]);
      const answeredRate = overview.total_questions
        ? Math.round((1 - overview.gap_rate) * 100) : null;
      main.append(
        el("div", { class: "grid cols-4", style: "margin-bottom:16px" },
          statCard(fmtNum(stats.total_chunks), "Indexed chunks"),
          statCard(fmtNum(stats.total_sources), "Sources"),
          statCard(fmtNum(overview.total_questions), `Questions · ${days}d`),
          statCard(answeredRate === null ? "—" : `${answeredRate}%`,
            overview.total_uncertain
              ? `Answered rate · ${overview.total_uncertain} uncertain`
              : "Answered rate",
            answeredRate !== null && answeredRate >= 80 ? "ok" : ""),
        ),
        el("div", { class: "grid cols-2" },
          el("div", { class: "card" },
            el("h3", {}, "Questions / day ",
              el("span", { class: "muted", style: "font-weight:400" },
                " blue = questions, red = gaps",
                overview.avg_useful_score !== null
                  ? ` · usefulness ${overview.avg_useful_score.toFixed(1)}/5`
                  : "")),
            overview.timeseries.length
              ? areaChart(overview.timeseries)
              : el("div", { class: "empty" }, "No searches logged yet")),
          el("div", { class: "card" },
            el("h3", {}, "Top sources ", el("span", { class: "muted" }, "by references")),
            usage.length
              ? barList(usage, { label: (r) => r.source, value: (r) => r.references })
              : el("div", { class: "empty" }, "No source references yet")),
          el("div", { class: "card" },
            el("h3", {}, "Coverage gaps"),
            gaps.length ? gapsTable(gaps.slice(0, 6), () => render()) :
              el("div", { class: "empty" }, "No coverage gaps 🎉")),
          el("div", { class: "card" },
            el("h3", {}, "Corpus"),
            corpusMeta(stats)),
        ),
      );
    } catch (err) { main.append(errorBox(err)); }
  };
  await render();
}

function statCard(value, label, kind) {
  return el("div", { class: "card" },
    el("div", { class: kind === "ok" ? "stat-value" : "stat-value" },
      kind === "ok" ? el("span", { class: "stat-badge", style: "font-size:22px;padding:4px 12px" }, value) : value),
    el("div", { class: "stat-label" }, label));
}

function corpusMeta(stats) {
  const row = (k, v) => el("tr", {}, el("td", { class: "muted" }, k), el("td", { class: "num" }, v));
  return el("table", {},
    row("Chunks with embedding", fmtNum(stats.chunks_with_embedding)),
    row("Chunks with summary", fmtNum(stats.chunks_with_summary)),
    row("Recipes", fmtNum(stats.total_recipes)),
    row("Last ingest", fmtDate(stats.last_ingest_at)),
  );
}

// ── Search playground ──

async function searchPage() {
  main.replaceChildren(pageHead("Search"));
  const input = el("input", { placeholder: "Ask the corpus anything…", autofocus: "" });
  const results = el("div", { class: "card", style: "padding:0" },
    el("div", { class: "empty" }, "Results appear here"));
  const run = async () => {
    const q = input.value.trim();
    if (!q) return;
    results.replaceChildren(el("div", { class: "empty" }, el("span", { class: "spinner" }), " Searching…"));
    try {
      const data = await api(`/v1/search?q=${encodeURIComponent(q)}&top_k=10`);
      if (!data.hits.length) {
        results.replaceChildren(el("div", { class: "empty" },
          "No hits — this query just became a coverage gap."));
        return;
      }
      results.replaceChildren(...data.hits.map((h) => {
        const body = el("div", { class: "hit" },
          el("div", { class: "hit-summary" }, h.summary),
          el("div", { class: "hit-source" }, h.source || "(no source)", "  ·  ",
            el("a", { href: "#", onclick: async (e) => {
              e.preventDefault();
              const existing = body.querySelector(".hit-full");
              if (existing) { existing.remove(); return; }
              try {
                const src = await api(`/v1/fetch_source/${h.episode_ids[0]}`);
                body.append(el("div", { class: "hit-full" }, src.content));
              } catch (err) { toast(err.message); }
            } }, "full chunk")));
        return body;
      }));
    } catch (err) { results.replaceChildren(errorBox(err)); }
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
  main.append(
    el("div", { class: "search-bar" }, input,
      el("button", { class: "primary-btn", onclick: run }, "Search")),
    results);
}

// ── Sources (importers) ──

async function sourcesPage() {
  main.replaceChildren(pageHead("Sources",
    el("button", { class: "primary-btn", onclick: () => addSourceDialog().then((ok) => ok && sourcesPage()) },
      "+ Add source")));
  const card = el("div", { class: "card", style: "padding:0" });
  main.append(card);
  try {
    const importers = await api("/v1/importers/");
    if (!importers.length) {
      card.append(el("div", { class: "empty" }, "No sources yet — add one to start indexing."));
      return;
    }
    card.append(el("table", {},
      el("tr", {}, ...["Source type", "Name", "Recipe", "Last run", "Status", ""].map((h) => el("th", {}, h))),
      ...importers.map((imp) => el("tr", { class: "clickable", onclick: () => importerDrawer(imp) },
        el("td", {}, srcIcon(imp.type), imp.type),
        el("td", {}, el("strong", {}, imp.name),
          imp.description ? el("div", { class: "muted", style: "font-size:12px" }, imp.description) : null),
        el("td", { class: "mono" }, imp.recipe),
        el("td", {}, fmtDate(imp.last_run_at)),
        el("td", {}, statusPill(imp)),
        el("td", { style: "text-align:right" },
          el("button", { class: "ghost-btn", onclick: async (e) => {
            e.stopPropagation();
            try {
              await api(`/v1/importers/${imp.id}/run`, { method: "POST" });
              toast(`Run queued: ${imp.name}`);
              setTimeout(sourcesPage, 800);
            } catch (err) { toast(err.message); }
          } }, "Run")),
      ))));
  } catch (err) { card.replaceChildren(errorBox(err)); }
}

function importerDrawer(imp) {
  document.querySelector(".drawer")?.remove();
  const drawer = el("div", { class: "drawer" });
  const close = () => drawer.remove();
  drawer.append(
    el("div", { class: "drawer-head" },
      el("h2", {}, srcIcon(imp.type), imp.name),
      el("button", { class: "icon-btn", onclick: close }, "✕")),
    el("div", { style: "display:flex;gap:8px;margin-bottom:16px" },
      statusPill(imp),
      el("span", { class: "pill" }, imp.recipe),
      el("span", { class: "pill" }, imp.workspace_id)),
    imp.last_error ? el("div", { class: "error-box" }, imp.last_error) : null,
    el("div", { style: "display:flex;gap:8px;margin-bottom:18px" },
      el("button", { class: "primary-btn", onclick: async () => {
        try { await api(`/v1/importers/${imp.id}/run`, { method: "POST" }); toast("Run queued"); }
        catch (err) { toast(err.message); }
      } }, "Run now"),
      el("button", { class: "ghost-btn", onclick: async () => {
        try {
          await api(`/v1/importers/${imp.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !imp.enabled }) });
          toast(imp.enabled ? "Disabled" : "Enabled"); close(); sourcesPage();
        } catch (err) { toast(err.message); }
      } }, imp.enabled ? "Disable" : "Enable"),
      el("button", { class: "danger-btn", onclick: async () => {
        if (!confirm(`Delete source "${imp.name}"? Indexed chunks stay in the corpus.`)) return;
        try { await api(`/v1/importers/${imp.id}`, { method: "DELETE" }); toast("Deleted"); close(); sourcesPage(); }
        catch (err) { toast(err.message); }
      } }, "Delete")),
    el("h3", { style: "font-size:13.5px" }, "Config"),
    el("pre", { class: "hit-full mono", style: "margin-top:0" }, JSON.stringify(imp.config, null, 2)),
    el("h3", { style: "font-size:13.5px" }, "Recent runs"),
  );
  const runsBox = el("div", {}, el("span", { class: "spinner" }));
  drawer.append(runsBox);
  document.body.append(drawer);
  api(`/v1/importers/${imp.id}/runs`).then((runs) => {
    runsBox.replaceChildren(runs.length
      ? el("table", {},
          el("tr", {}, ...["Started", "Status", "Items", "Chunks"].map((h) => el("th", {}, h))),
          ...runs.map((r) => el("tr", {},
            el("td", {}, fmtDate(r.started_at)),
            el("td", {}, pill(r.status, r.status === "succeeded" ? "ok" : r.status === "failed" ? "err" : r.status === "running" ? "run" : "")),
            el("td", { class: "num" }, `${r.items_done}${r.items_total ? "/" + r.items_total : ""}`),
            el("td", { class: "num" }, fmtNum(r.chunks_added)))))
      : el("div", { class: "empty" }, "No runs yet"));
  }).catch((err) => runsBox.replaceChildren(errorBox(err)));
}

// Add-source wizard: pick type → JSON-schema form (+secrets) → optional discovery → save.
async function addSourceDialog() {
  let types;
  try { types = await api("/v1/importers/types"); }
  catch (err) { toast(err.message); return false; }

  return new Promise((resolve) => {
    const dlg = el("dialog", {});
    const body = el("div", { class: "dialog-body" });
    dlg.append(body);
    document.body.append(dlg);
    const done = (ok) => { dlg.close(); dlg.remove(); resolve(ok); };

    const renderPicker = () => {
      body.replaceChildren(
        el("h2", {}, "Add source"),
        el("div", { style: "display:grid;grid-template-columns:1fr 1fr;gap:8px;max-height:420px;overflow:auto" },
          ...types.map((t) => el("button", { class: "ghost-btn", style: "text-align:left;padding:11px",
            onclick: () => renderForm(t) },
            el("div", {}, srcIcon(t.type), el("strong", {}, t.display_name)),
            el("div", { class: "muted", style: "font-size:12px;margin-top:4px" },
              t.description.slice(0, 90))))),
        el("div", { class: "dialog-actions" },
          el("button", { class: "ghost-btn", onclick: () => done(false) }, "Cancel")));
    };

    const renderForm = (t) => {
      const props = t.config_schema?.properties || {};
      const required = new Set(t.config_schema?.required || []);
      const cfgInputs = {}, secInputs = {};
      const fields = Object.entries(props).map(([key, schema]) => {
        const input = schema.type === "boolean"
          ? el("select", {}, el("option", { value: "" }, "—"),
              el("option", { value: "true" }, "true"), el("option", { value: "false" }, "false"))
          : el("input", { placeholder: schema.description || "",
              ...(schema.default !== undefined ? { value: String(schema.default) } : {}) });
        cfgInputs[key] = { input, schema };
        return el("label", {}, `${key}${required.has(key) ? " *" : ""}`,
          schema.description ? el("span", { class: "muted" }, ` — ${schema.description.slice(0, 80)}`) : null,
          input);
      });
      const secretFields = t.secret_keys.map((key) => {
        const input = el("input", { type: "password", placeholder: "stored encrypted" });
        secInputs[key] = input;
        return el("label", {}, `${key} 🔑`, input);
      });
      const nameInput = el("input", { placeholder: "My docs" });
      const recipeInput = el("input", { value: t.type, placeholder: "recipe tag" });
      const errBox = el("div", {});
      const discoveryBox = el("div", {});

      const collect = () => {
        const config = {};
        for (const [key, { input, schema }] of Object.entries(cfgInputs)) {
          const raw = input.value.trim();
          if (!raw) continue;
          if (schema.type === "integer" || schema.type === "number") config[key] = Number(raw);
          else if (schema.type === "boolean") config[key] = raw === "true";
          else if (schema.type === "array") config[key] = raw.split(",").map((s) => s.trim()).filter(Boolean);
          else config[key] = raw;
        }
        const secrets = {};
        for (const [key, input] of Object.entries(secInputs)) {
          if (input.value) secrets[key] = input.value;
        }
        return { config, secrets };
      };

      const applyPatch = (patch) => {
        for (const [key, val] of Object.entries(patch)) {
          const f = cfgInputs[key];
          if (f) f.input.value = Array.isArray(val) ? val.join(", ") : String(val);
        }
      };

      body.replaceChildren(
        el("h2", {}, srcIcon(t.type), `Add ${t.display_name}`),
        errBox,
        el("label", {}, "Name *", nameInput),
        el("label", {}, "Recipe *", recipeInput),
        ...fields, ...secretFields,
        t.supports_discovery
          ? el("div", { style: "margin-bottom:10px" },
              el("button", { class: "ghost-btn", onclick: async (e) => {
                e.preventDefault();
                const { config, secrets } = collect();
                discoveryBox.replaceChildren(el("span", { class: "spinner" }));
                try {
                  const out = await api("/v1/importers/discover", {
                    method: "POST", body: JSON.stringify({ type: t.type, config, secrets }) });
                  discoveryBox.replaceChildren(
                    el("div", { class: "muted", style: "margin:8px 0 4px" }, "Pick what to index:"),
                    ...out.items.map((item) => el("button", { class: "ghost-btn",
                      style: "display:block;width:100%;text-align:left;margin-bottom:6px",
                      onclick: (ev) => { ev.preventDefault(); applyPatch(item.config_patch); toast(`Applied: ${item.name}`); } },
                      el("strong", {}, item.name), ` · ${item.kind}`,
                      item.hint ? el("span", { class: "muted" }, ` — ${item.hint}`) : null)));
                } catch (err) { discoveryBox.replaceChildren(errorBox(err)); }
              } }, "🔍 Discover"),
              discoveryBox)
          : null,
        el("div", { class: "dialog-actions" },
          el("button", { class: "ghost-btn", onclick: renderPicker }, "Back"),
          el("button", { class: "primary-btn", onclick: async () => {
            const { config, secrets } = collect();
            try {
              await api("/v1/importers/", { method: "POST", body: JSON.stringify({
                type: t.type, name: nameInput.value.trim(),
                recipe: recipeInput.value.trim() || t.type, config, secrets }) });
              toast("Source created");
              done(true);
            } catch (err) { errBox.replaceChildren(errorBox(err)); }
          } }, "Create source")));
    };

    renderPicker();
    dlg.showModal();
    dlg.addEventListener("cancel", () => done(false));
  });
}

// ── Coverage gaps ──

const GAP_STATUSES = ["open", "planned", "resolved", "ignored"];

function gapsTable(gaps, onChange) {
  return el("table", {},
    el("tr", {}, ...["Topic", "Asks", "Last asked", "Status"].map((h) => el("th", {}, h))),
    ...gaps.map((g) => el("tr", {},
      el("td", {}, g.query),
      el("td", {}, el("span", { class: `count-badge${g.count >= 5 ? " hot" : ""}` }, fmtNum(g.count))),
      el("td", {}, fmtDate(g.last_asked_at)),
      el("td", {}, el("select", { onchange: async (e) => {
        try {
          await api("/v1/analytics/gaps/status", { method: "PATCH",
            body: JSON.stringify({ query: g.query, status: e.target.value }) });
          toast(`"${g.query.slice(0, 40)}" → ${e.target.value}`);
          onChange?.();
        } catch (err) { toast(err.message); }
      } }, ...GAP_STATUSES.map((s) =>
        el("option", { value: s, ...(s === g.status ? { selected: "" } : {}) }, s)))))));
}

async function gapsPage() {
  let days = 30, includeResolved = false;
  const render = async () => {
    const toggle = el("label", { style: "margin:0;display:flex;align-items:center;gap:6px" },
      el("input", { type: "checkbox", style: "width:auto", ...(includeResolved ? { checked: "" } : {}),
        onchange: (e) => { includeResolved = e.target.checked; render(); } }),
      "show resolved");
    main.replaceChildren(pageHead("Coverage gaps", toggle, daysFilter(days, (d) => { days = d; render(); })));
    const card = el("div", { class: "card", style: "padding:0 18px" });
    main.append(
      el("p", { class: "muted", style: "margin-top:-8px" },
        "Queries that returned zero hits — what agents ask that the corpus can't answer. Feed these to your importers."),
      card);
    try {
      const gaps = await api(`/v1/analytics/gaps?days=${days}&limit=100&include_resolved=${includeResolved}`);
      card.replaceChildren(gaps.length ? gapsTable(gaps, render)
        : el("div", { class: "empty" }, "No coverage gaps in this window 🎉"));
    } catch (err) { card.replaceChildren(errorBox(err)); }
  };
  await render();
}

// ── Top questions ──

async function questionsPage() {
  let days = 30;
  const render = async () => {
    main.replaceChildren(pageHead("Top questions", daysFilter(days, (d) => { days = d; render(); })));
    const card = el("div", { class: "card", style: "padding:0 18px" });
    main.append(card);
    try {
      const rows = await api(`/v1/analytics/top-queries?days=${days}&limit=100`);
      card.replaceChildren(rows.length
        ? el("table", {},
            el("tr", {}, ...["Question", "Asks", "Avg hits", "Gap asks", "Last asked"].map((h) => el("th", {}, h))),
            ...rows.map((r) => el("tr", {},
              el("td", {}, r.query),
              el("td", {}, el("span", { class: "count-badge" }, fmtNum(r.count))),
              el("td", { class: "num" }, r.avg_hits.toFixed(1)),
              el("td", { class: "num" }, r.gap_count ? el("span", { class: "count-badge hot" }, r.gap_count) : "—"),
              el("td", {}, fmtDate(r.last_asked_at)))))
        : el("div", { class: "empty" }, "No searches logged yet"));
    } catch (err) { card.replaceChildren(errorBox(err)); }
  };
  await render();
}

// ── Source analytics ──

async function sourceAnalyticsPage() {
  let days = 30;
  const render = async () => {
    main.replaceChildren(pageHead("Source analytics", daysFilter(days, (d) => { days = d; render(); })));
    try {
      const [usage, corpus] = await Promise.all([
        api(`/v1/analytics/source-usage?days=${days}&limit=100`),
        api("/v1/corpus/sources?limit=200&order=chunks"),
      ]);
      const refBySource = new Map(usage.map((u) => [u.source, u.references]));
      main.append(el("div", { class: "grid cols-2" },
        el("div", { class: "card" },
          el("h3", {}, "References ", el("span", { class: "muted" }, "searches that surfaced the source")),
          usage.length ? barList(usage, { label: (r) => r.source, value: (r) => r.references })
            : el("div", { class: "empty" }, "No references yet")),
        el("div", { class: "card", style: "padding:0 18px" },
          el("h3", { style: "padding:16px 0 0" }, "Corpus vs usage ",
            el("span", { class: "muted" }, "chunks indexed vs references — 0 refs = dead weight")),
          el("table", {},
            el("tr", {}, ...["Source", "Chunks", "Refs", "Last ingest"].map((h) => el("th", {}, h))),
            ...corpus.map((s) => el("tr", {},
              el("td", { title: s.source, style: "max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, s.source),
              el("td", { class: "num" }, fmtNum(s.chunk_count)),
              el("td", { class: "num" }, refBySource.has(s.source)
                ? fmtNum(refBySource.get(s.source))
                : el("span", { class: "muted" }, "0")),
              el("td", {}, fmtDate(s.last_ingest_at))))))));
    } catch (err) { main.append(errorBox(err)); }
  };
  await render();
}

// ── API keys ──

async function keysPage() {
  main.replaceChildren(pageHead("API keys",
    el("button", { class: "primary-btn", onclick: async () => {
      const name = prompt("Key name (e.g. 'ci-bot', 'claude-code'):");
      if (!name) return;
      try {
        const created = await api("/v1/keys/", { method: "POST", body: JSON.stringify({ name }) });
        prompt("API key secret (shown ONCE — store it now):", created.secret);
        keysPage();
      } catch (err) { toast(err.message); }
    } }, "+ Create key")));
  const card = el("div", { class: "card", style: "padding:0" });
  main.append(
    el("p", { class: "muted", style: "margin-top:-8px" },
      "Keys are bound to the current workspace. Send as ",
      el("span", { class: "mono" }, "Authorization: Bearer lh_…"),
      " on /v1/search and /mcp/."),
    card);
  try {
    const keys = await api("/v1/keys/");
    card.replaceChildren(keys.length
      ? el("table", {},
          el("tr", {}, ...["Name", "Workspace", "Created", "Last used", "Status", ""].map((h) => el("th", {}, h))),
          ...keys.map((k) => el("tr", {},
            el("td", {}, el("strong", {}, k.name)),
            el("td", {}, el("span", { class: "pill" }, k.workspace_id)),
            el("td", {}, fmtDate(k.created_at)),
            el("td", {}, fmtDate(k.last_used_at)),
            el("td", {}, k.revoked_at ? pill("Revoked", "err") : pill("Active", "ok")),
            el("td", { style: "text-align:right" },
              k.revoked_at ? null
                : el("button", { class: "danger-btn", onclick: async () => {
                    if (!confirm(`Revoke key "${k.name}"? Clients using it lose access immediately.`)) return;
                    try { await api(`/v1/keys/${k.id}`, { method: "DELETE" }); keysPage(); }
                    catch (err) { toast(err.message); }
                  } }, "Revoke")))))
      : el("div", { class: "empty" }, "No API keys — create one to give an agent private access."));
  } catch (err) { card.replaceChildren(errorBox(err)); }
}

// ── Workspaces ──

async function workspacesPage() {
  main.replaceChildren(pageHead("Workspaces",
    el("button", { class: "primary-btn", onclick: async () => {
      const id = prompt("Workspace id (e.g. 'team-platform'):");
      if (!id) return;
      try {
        await api(`/v1/workspaces/${encodeURIComponent(id)}`, {
          method: "PUT", body: JSON.stringify({ require_auth: false }) });
        workspacesPage();
      } catch (err) { toast(err.message); }
    } }, "+ Register workspace")));
  const card = el("div", { class: "card", style: "padding:0" });
  main.append(
    el("p", { class: "muted", style: "margin-top:-8px" },
      "Require keys = keyless callers get 401 for this workspace even while the instance default stays open."),
    card);
  try {
    const rows = await api("/v1/workspaces/");
    card.replaceChildren(rows.length
      ? el("table", {},
          el("tr", {}, ...["Workspace", "Keyless access", "Created", ""].map((h) => el("th", {}, h))),
          ...rows.map((w) => el("tr", {},
            el("td", {}, el("strong", {}, w.id),
              w.description ? el("div", { class: "muted", style: "font-size:12px" }, w.description) : null),
            el("td", {}, w.require_auth ? pill("Keys required", "warn") : pill("Open", "ok")),
            el("td", {}, fmtDate(w.created_at)),
            el("td", { style: "text-align:right" },
              el("button", { class: "ghost-btn", onclick: async () => {
                try {
                  await api(`/v1/workspaces/${encodeURIComponent(w.id)}`, {
                    method: "PUT",
                    body: JSON.stringify({ require_auth: !w.require_auth }) });
                  toast(`${w.id}: ${w.require_auth ? "open to keyless" : "keys required"}`);
                  workspacesPage();
                } catch (err) { toast(err.message); }
              } }, w.require_auth ? "Allow keyless" : "Require keys")))))
      : el("div", { class: "empty" },
          "No registered workspaces — tenants still work unregistered; register one to manage its auth policy."));
  } catch (err) { card.replaceChildren(errorBox(err)); }
}

// ── Webhooks ──

async function webhooksPage() {
  main.replaceChildren(pageHead("Webhooks",
    el("button", { class: "primary-btn", onclick: () => addWebhookDialog().then((ok) => ok && webhooksPage()) },
      "+ Add webhook")));
  const card = el("div", { class: "card", style: "padding:0" });
  main.append(card);
  try {
    const hooks = await api("/v1/webhooks/");
    card.replaceChildren(hooks.length
      ? el("table", {},
          el("tr", {}, ...["URL", "Events", "Last delivery", "Status", ""].map((h) => el("th", {}, h))),
          ...hooks.map((wh) => el("tr", {},
            el("td", { class: "mono" }, wh.url),
            el("td", {}, wh.events.map((ev) => el("span", { class: "pill", style: "margin-right:4px" }, ev))),
            el("td", {}, fmtDate(wh.last_delivery_at)),
            el("td", {}, !wh.enabled ? pill("Disabled", "")
              : wh.last_error ? pill(`${wh.last_status ?? "err"}`, "err")
              : wh.last_status ? pill(String(wh.last_status), "ok") : pill("Idle", "")),
            el("td", { style: "text-align:right;white-space:nowrap" },
              el("button", { class: "ghost-btn", onclick: async () => {
                try { await api(`/v1/webhooks/${wh.id}/test`, { method: "POST" }); toast("Test event queued"); }
                catch (err) { toast(err.message); }
              } }, "Test"),
              " ",
              el("button", { class: "ghost-btn", title: "Requeue deliveries whose retries were exhausted",
                onclick: async () => {
                  try {
                    const r = await api(`/v1/webhooks/${wh.id}/deliveries/requeue-dead`, { method: "POST" });
                    toast(r.requeued ? `Requeued ${r.requeued} dead deliveries` : "No dead deliveries");
                  } catch (err) { toast(err.message); }
                } }, "Requeue dead"),
              " ",
              el("button", { class: "danger-btn", onclick: async () => {
                if (!confirm(`Delete webhook ${wh.url}?`)) return;
                try { await api(`/v1/webhooks/${wh.id}`, { method: "DELETE" }); webhooksPage(); }
                catch (err) { toast(err.message); }
              } }, "Delete")))))
      : el("div", { class: "empty" }, "No webhooks — subscribe a URL to importer.run.* events."));
  } catch (err) { card.replaceChildren(errorBox(err)); }
}

async function addWebhookDialog() {
  return new Promise((resolve) => {
    const url = el("input", { placeholder: "https://example.com/hooks/lighthouse" });
    const events = el("input", { value: "*", placeholder: "* or importer.run.completed, …" });
    const errBox = el("div", {});
    const dlg = el("dialog", {}, el("div", { class: "dialog-body" },
      el("h2", {}, "Add webhook"), errBox,
      el("label", {}, "URL *", url),
      el("label", {}, "Events (comma-separated)", events),
      el("div", { class: "dialog-actions" },
        el("button", { class: "ghost-btn", onclick: () => { dlg.close(); dlg.remove(); resolve(false); } }, "Cancel"),
        el("button", { class: "primary-btn", onclick: async () => {
          try {
            const created = await api("/v1/webhooks/", { method: "POST", body: JSON.stringify({
              url: url.value.trim(),
              events: events.value.split(",").map((s) => s.trim()).filter(Boolean) }) });
            prompt("Webhook secret (shown once — store it now):", created.secret);
            dlg.close(); dlg.remove(); resolve(true);
          } catch (err) { errBox.replaceChildren(errorBox(err)); }
        } }, "Create"))));
    document.body.append(dlg);
    dlg.showModal();
    dlg.addEventListener("cancel", () => { dlg.remove(); resolve(false); });
  });
}

// ───────────────────────── Router & settings ─────────────────────────

const routes = {
  "/": dashboardPage,
  "/search": searchPage,
  "/sources": sourcesPage,
  "/keys": keysPage,
  "/workspaces": workspacesPage,
  "/webhooks": webhooksPage,
  "/gaps": gapsPage,
  "/questions": questionsPage,
  "/source-analytics": sourceAnalyticsPage,
};

function navigate() {
  document.querySelector(".drawer")?.remove();
  const path = location.hash.replace(/^#/, "") || "/";
  const page = routes[path] || dashboardPage;
  for (const a of document.querySelectorAll("nav a")) {
    a.classList.toggle("active", a.dataset.route === path);
  }
  page().catch((err) => main.replaceChildren(errorBox(err)));
}

window.addEventListener("hashchange", navigate);

$("#settings-btn").addEventListener("click", () => {
  $("#set-workspace").value = localStorage.getItem(LS_WORKSPACE) || "";
  $("#set-token").value = localStorage.getItem(LS_TOKEN) || "";
  $("#settings-dialog").showModal();
});
$("#settings-dialog").addEventListener("close", () => {
  if ($("#settings-dialog").returnValue !== "save") return;
  const ws = $("#set-workspace").value.trim();
  const token = $("#set-token").value.trim();
  ws ? localStorage.setItem(LS_WORKSPACE, ws) : localStorage.removeItem(LS_WORKSPACE);
  token ? localStorage.setItem(LS_TOKEN, token) : localStorage.removeItem(LS_TOKEN);
  toast("Connection saved");
  navigate();
});

navigate();
