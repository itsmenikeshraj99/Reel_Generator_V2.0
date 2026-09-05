/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // Brand colors — fixed in both themes (the gradient is the
        // brand, not a theme token).
        primary:   "var(--primary)",
        secondary: "var(--secondary)",
        // Phase 12 PR 2: theme tokens. The `dark: "#121212"` literal
        // is gone; existing `bg-dark` usages get renamed to `bg-bg` in
        // PR 7's mechanical swap.
        bg:            "rgb(var(--bg) / <alpha-value>)",
        surface:       "rgb(var(--surface) / <alpha-value>)",
        "surface-2":   "rgb(var(--surface-2) / <alpha-value>)",
        border:        "rgb(var(--border) / <alpha-value>)",
        text:          "rgb(var(--text) / <alpha-value>)",
        "text-muted":  "rgb(var(--text-muted) / <alpha-value>)",
        "text-subtle": "rgb(var(--text-subtle) / <alpha-value>)",
      },
      // Phase 11 animations. Used by Toast (toast-in), AppShell drawer
      // (toast-in reused for fade), and any "in progress" indicators.
      keyframes: {
        "toast-in": {
          "0%": { opacity: "0", transform: "translateY(-8px) scale(0.98)" },
          "100%": { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        "pulse-stage": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.5" },
        },
        "gradient-shift": {
          "0%, 100%": { backgroundPosition: "0% 50%" },
          "50%": { backgroundPosition: "100% 50%" },
        },
      },
      animation: {
        "toast-in": "toast-in 220ms ease-out",
        "pulse-stage": "pulse-stage 1.6s ease-in-out infinite",
        "gradient-shift": "gradient-shift 6s ease infinite",
      },
    },
  },
  plugins: [],
}
