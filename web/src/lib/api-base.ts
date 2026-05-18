// Where SSR-side fetches reach the Python API. Two reasons this
// has to be a runtime read:
//
//   1. PUBLIC_* vars are inlined by Vite at build time. If the
//      Docker build doesn't have the value set, the bundled
//      string defaults to the public host — and the public
//      ingress sends /search to the HTML page, not the API. SSR
//      then receives HTML, fails to parse JSON, and renders an
//      empty page.
//
//   2. The deploy already injects PUBLIC_LIGHTHOUSE_API_BASE at
//      runtime (in-cluster Service hostname). Reading it via
//      process.env honors that value instead of the build-time
//      default.
export const API_BASE =
  process.env.PUBLIC_LIGHTHOUSE_API_BASE ||
  "http://lighthouse-api.lighthouse.svc.cluster.local";
