/** @type {import("tailwindcss").Config} */
export default {
  content: ["./src/**/*.{astro,html,js,jsx,ts,tsx,md,mdx}"],
  theme: {
    extend: {
      // Newspaper-leaning typography stack — Playfair Display for
      // headers (transitional serif, slight contrast), Lora as body
      // serif fallback, IBM Plex Mono for data/code.
      fontFamily: {
        display: [
          '"Playfair Display"',
          "Georgia",
          '"Times New Roman"',
          "serif",
        ],
        serif: ['"Lora"', "Georgia", '"Times New Roman"', "serif"],
        mono: [
          '"IBM Plex Mono"',
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "monospace",
        ],
        sans: [
          '"Inter"',
          "-apple-system",
          "BlinkMacSystemFont",
          "system-ui",
          "sans-serif",
        ],
      },
      colors: {
        ink: "#111111",        // body text (slightly off-black, easier on eyes)
        paper: "#f8f5ee",      // newsprint cream
        rule: "#1a1a1a",       // hairline separator
        accent: "#b3000c",     // crimson — masthead bars + section flags
        muted: "#6a6a6a",      // bylines / metadata
        soft: "#ece6d8",       // section-background tint
      },
      maxWidth: {
        broadsheet: "1280px",
        column: "32rem",
      },
      letterSpacing: {
        masthead: "0.04em",
        flag: "0.18em",
      },
    },
  },
  plugins: [],
};
