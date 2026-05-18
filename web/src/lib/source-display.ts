// Source IDs in the corpus look like
//   "rfc-http-semantics:https://datatracker.ietf.org/doc/html/rfc9110"
//   "ml-openai-cookbook:github-tree:openai/openai-cookbook@9b4e627:examples/Customizing_embeddings.ipynb"
// — recipe slug, then a colon, then either a URL or a github-tree
// reference. Renderers want a clean two-line presentation: the
// recipe slug on top, the destination URL underneath, the
// destination clickable when it resolves to a real URL.

export interface ParsedSource {
  display: string;         // human-readable URL / path
  href: string | null;     // clickable target, or null if not resolvable
}

// After the multi-recipe migration `source` is the canonical
// upstream identifier (URL or github-tree ref) without any recipe
// prefix. Recipe membership lives in chunks.recipes[] instead.
export function parseSource(s: string): ParsedSource {
  if (!s) return { display: "", href: null };

  // github-tree:owner/repo@sha:path
  if (s.startsWith("github-tree:")) {
    const tail = s.slice("github-tree:".length);
    const at = tail.indexOf("@");
    const colon = tail.indexOf(":", at < 0 ? 0 : at);
    if (at > 0 && colon > at) {
      const ownerRepo = tail.slice(0, at);
      const sha = tail.slice(at + 1, colon);
      const path = tail.slice(colon + 1);
      return {
        display: `${ownerRepo} · ${path}`,
        href: `https://github.com/${ownerRepo}/blob/${sha}/${path}`,
      };
    }
    return { display: tail, href: null };
  }

  // gh-release:owner/repo:tag — link to the GitHub release page.
  if (s.startsWith("gh-release:")) {
    const tail = s.slice("gh-release:".length);
    const colon = tail.lastIndexOf(":");
    if (colon > 0) {
      const ownerRepo = tail.slice(0, colon);
      const tag = tail.slice(colon + 1);
      return {
        display: `${ownerRepo} · ${tag}`,
        href: `https://github.com/${ownerRepo}/releases/tag/${tag}`,
      };
    }
    return { display: tail, href: null };
  }

  // Plain URL.
  if (/^https?:\/\//.test(s)) {
    return { display: s, href: s };
  }
  return { display: s, href: null };
}
