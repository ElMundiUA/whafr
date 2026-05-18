// Two shared helpers used by both the chunk-detail page and the
// ChunkCard component:
//
// * `prettifyChunkName` — turns a path-shaped name like
//   `content/en/docs/concepts/configuration/liveness-readiness-startup-probes.md`
//   into "Liveness Readiness Startup Probes" so it reads as a
//   headline instead of a path.
//
// * `cleanChunkContent` — strips YAML front-matter, HTML comments,
//   and Hugo/Jekyll shortcodes from the body so the chunk page can
//   render the actual prose rather than ingest scaffolding.

import { marked } from "marked";

export function prettifyChunkName(s: string): string {
  if (!s) return s;
  // Strip any " (part N/M)" / " (N/M)" suffix the chunker appends,
  // then put it back at the end after cleaning the base.
  const partMatch = s.match(/\s\((?:part\s+)?\d+\/\d+\)\s*$/i);
  const base = (partMatch ? s.slice(0, partMatch.index) : s).trim();

  const pathy =
    (/\//.test(base) && !/\s/.test(base.slice(0, 40))) ||
    /\.(md|mdx|rst|txt|yml|yaml|html?)$/i.test(base);

  if (!pathy) return s;

  const last = base.split("/").pop() ?? base;
  const noExt = last.replace(/\.(md|mdx|rst|txt|yml|yaml|html?)$/i, "");
  const cleaned =
    noExt.toLowerCase() === "index"
      ? base.split("/").slice(-2, -1)[0] ?? noExt
      : noExt;
  const titled = cleaned
    .replace(/[-_]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase());
  return partMatch ? `${titled} ${partMatch[0].trim()}` : titled;
}

export function cleanChunkContent(raw: string): string {
  if (!raw) return raw;
  let s = raw;

  // 1. YAML / TOML front-matter at the very top.
  s = s.replace(/^---\n[\s\S]*?\n---\s*\n?/, "");
  s = s.replace(/^\+\+\+\n[\s\S]*?\n\+\+\+\s*\n?/, "");

  // 2. HTML comments (Hugo overview/body markers, "TODO" notes).
  s = s.replace(/<!--[\s\S]*?-->/g, "");

  // 3. Hugo shortcodes — `{{< ... >}}` and `{{% ... %}}`. Replace
  // with the inner text content where there's an obvious label
  // (e.g. `text="kubelet"`), otherwise drop.
  s = s.replace(/\{\{[<%][\s\S]*?[>%]\}\}/g, (m) => {
    const tx = m.match(/text\s*=\s*"([^"]+)"/);
    return tx ? tx[1] : "";
  });

  // 4. Mustache / Jekyll `{% ... %}` blocks.
  s = s.replace(/\{%[\s\S]*?%\}/g, "");

  // 5. Trailing chunk pagination breadcrumb if any.
  s = s.replace(/\n+--- *\(continued in part [^)]*\) *$/i, "");

  // 6. Collapse runs of 3+ blank lines.
  s = s.replace(/\n{3,}/g, "\n\n");

  return s.trim();
}

const renderer = new marked.Renderer();
// Defang any inline HTML the source markdown may carry.
renderer.html = () => "";

marked.setOptions({
  gfm: true,
  breaks: false,
  renderer,
});

export function renderMarkdown(raw: string): string {
  const cleaned = cleanChunkContent(raw);
  // marked's typings vary; cast to string since we never pass async.
  return marked.parse(cleaned, { async: false }) as string;
}
