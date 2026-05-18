// Source IDs in the corpus look like
//   "rfc-http-semantics:https://datatracker.ietf.org/doc/html/rfc9110"
//   "ml-openai-cookbook:github-tree:openai/openai-cookbook@9b4e627:examples/Customizing_embeddings.ipynb"
// — recipe slug, then a colon, then either a URL or a github-tree
// reference. Renderers want a clean two-line presentation: the
// recipe slug on top, the destination URL underneath, the
// destination clickable when it resolves to a real URL.

export interface ParsedSource {
  recipe: string;          // e.g. "rfc-http-semantics"
  display: string;         // human-readable URL / path
  href: string | null;     // clickable target, or null if not resolvable
}

export function parseSource(s: string): ParsedSource {
  if (!s) return { recipe: "", display: "", href: null };
  const idx = s.indexOf(":");
  if (idx < 0) return { recipe: s, display: "", href: null };
  const recipe = s.slice(0, idx);
  const rest = s.slice(idx + 1);

  // github-tree:owner/repo@sha:path
  if (rest.startsWith("github-tree:")) {
    const tail = rest.slice("github-tree:".length);
    // "openai/openai-cookbook@9b4e627:examples/foo.ipynb"
    const at = tail.indexOf("@");
    const colon = tail.indexOf(":", at < 0 ? 0 : at);
    if (at > 0 && colon > at) {
      const ownerRepo = tail.slice(0, at);
      const sha = tail.slice(at + 1, colon);
      const path = tail.slice(colon + 1);
      return {
        recipe,
        display: `${ownerRepo} · ${path}`,
        href: `https://github.com/${ownerRepo}/blob/${sha}/${path}`,
      };
    }
    return { recipe, display: tail, href: null };
  }

  // Plain URL.
  if (/^https?:\/\//.test(rest)) {
    return { recipe, display: rest, href: rest };
  }

  return { recipe, display: rest, href: null };
}
