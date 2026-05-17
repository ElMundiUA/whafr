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
        // Harbor Gang palette
        ink: "#0b1020",        // deep navy bg
        mist: "#e8f4ff",       // body text on dark
        champagne: "#cfa96b",  // primary accent / Harbor Gang gold
        azure: "#5bb8ff",      // Lighthouse-specific accent
        violet: "#9d7dff",     // Buzz accent (cross-product nav)
        coral: "#ff6b75",      // danger / warnings
        // Legacy newspaper-class aliases so the old classes keep
        // resolving but render on the dark theme. Phased out as
        // each page gets cleaned up.
        paper: "#0b1020",      // was newsprint cream; now ink
        rule: "rgba(232,244,255,0.12)",  // hairline = white/12 on dark
        accent: "#cfa96b",     // was crimson; now champagne
        muted: "rgba(232,244,255,0.55)", // mist-on-mist
        soft: "rgba(255,255,255,0.04)",  // glass-card tint
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
        card: "0 24px 80px -32px rgba(11, 16, 32, 0.45)",
        glow: "0 0 60px -15px rgba(207, 169, 107, 0.50)",
        "glow-sm": "0 0 30px -8px rgba(207, 169, 107, 0.40)",
        "glow-azure": "0 0 60px -15px rgba(91, 184, 255, 0.40)",
      },
    },
  },
  plugins: [],
};
