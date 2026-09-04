/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        primary: "#FF0050", // TikTok-like Red
        secondary: "#00F2EA", // TikTok-like Cyan
        dark: "#121212",
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
