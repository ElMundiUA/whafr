/** @type {import("tailwindcss").Config} */
export default {
  content: ["./src/**/*.{astro,html,js,jsx,ts,tsx,md,mdx}"],
  darkMode: "class",
  theme: {
    extend: {
      // Harbor Gang stack: Plus Jakarta Sans for display, DM Sans
      // for body. No serif. Loaded from Google Fonts in global.css.
      fontFamily: {
        display: [
          '"Plus Jakarta Sans"',
          "ui-sans-serif",
          "system-ui",
          "sans-serif",
        ],
        sans: [
          '"DM Sans"',
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "BlinkMacSystemFont",
          "sans-serif",
        ],
        // Kept under `serif` so the old `font-serif` classes don't
        // 404 to browser default — points at DM Sans like the body.
        serif: ['"DM Sans"', "ui-sans-serif", "system-ui", "sans-serif"],
        mono: [
          '"JetBrains Mono"',
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "monospace",
        ],
      },
      colors: {
        // Aligned with ship.elmundi.com tokens (apps/landing/tailwind.config.ts).
        // Same ink/mist/champagne; coral matches Ship's #ff5c6c (more saturated
        // than the #ff6b75 we had). `aqua` is Ship's brand-key alias for the
        // champagne gold — kept for cross-product parity.
        ink: "#0b1020",        // deep navy bg
        mist: "#e8f4ff",       // body text on dark
        champagne: "#cfa96b",  // primary accent / Harbor Gang gold
        aqua: "#cfa96b",       // Ship alias for the same colour
        coral: "#ff5c6c",      // danger / warnings (Ship-matched)
        lilac: "#b388ff",      // secondary accent (Ship)
        sun: "#ffd54a",        // tertiary accent (Ship)
        azure: "#5bb8ff",      // Lighthouse-specific (kept)
        violet: "#9d7dff",     // Buzz cross-product nav (kept)
        // Legacy newspaper-class aliases so the old classes keep
        // resolving on dark; phased out per page.
        paper: "#0b1020",
        rule: "rgba(232,244,255,0.12)",
        accent: "#cfa96b",
        muted: "rgba(232,244,255,0.55)",
        soft: "rgba(255,255,255,0.04)",
      },
      maxWidth: {
        broadsheet: "1280px",
        column: "32rem",
      },
      letterSpacing: {
        masthead: "-0.02em",   // tighter for Plus Jakarta Sans
        flag: "0.22em",        // Harbor Gang kicker spacing
      },
      boxShadow: {
        // Ship-matched glow (apps/landing/tailwind.config.ts).
        card: "0 24px 80px -32px rgba(11, 16, 32, 0.45)",
        glow: "0 0 80px -20px rgba(207, 169, 107, 0.40)",
        "glow-sm": "0 0 30px -8px rgba(207, 169, 107, 0.40)",
        "glow-azure": "0 0 60px -15px rgba(91, 184, 255, 0.40)",
      },
      backgroundImage: {
        "grid-fade":
          "linear-gradient(to right, rgba(255,255,255,0.06) 1px, transparent 1px), linear-gradient(to bottom, rgba(255,255,255,0.06) 1px, transparent 1px)",
      },
    },
  },
  plugins: [],
};
