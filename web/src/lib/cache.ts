// Tiny in-memory TTL cache. Lives in the Node process — shared
// across requests, gone on pod restart. Used for SSR fetches whose
// result is shared by every visitor (front-page teasers, section
// rosters) so we don't fan out 6 API calls per page-view and trip
// the OpenAI rate limit.

interface Entry<T> { value: T; expires: number; }

const store = new Map<string, Entry<unknown>>();

export async function cached<T>(
  key: string,
  ttlSeconds: number,
  load: () => Promise<T>,
): Promise<T> {
  const now = Date.now();
  const hit = store.get(key) as Entry<T> | undefined;
  if (hit && hit.expires > now) return hit.value;
  const value = await load();
  store.set(key, { value, expires: now + ttlSeconds * 1000 });
  return value;
}
