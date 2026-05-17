import { defineConfig } from "astro/config";
import tailwind from "@astrojs/tailwind";
import react from "@astrojs/react";
import node from "@astrojs/node";

// Astro 5 collapsed `hybrid` into `static` — `prerender = false` on a
// page now triggers SSR for it inside an otherwise-static build. The
// node adapter handles those SSR routes; static pages are emitted as
// HTML at build time.
export default defineConfig({
  output: "static",
  adapter: node({ mode: "standalone" }),
  site: "https://lighthouse.harborgang.com",
  integrations: [
    tailwind({ applyBaseStyles: false }),
    react(),
  ],
  server: {
    port: 4321,
    host: "0.0.0.0",
  },
  vite: {
    server: {
      // When developing against the deployed API:
      //   PUBLIC_LIGHTHOUSE_API_BASE=https://lighthouse.harborgang.com
      // When developing against local:
      //   PUBLIC_LIGHTHOUSE_API_BASE=http://localhost:8000
    },
  },
});
