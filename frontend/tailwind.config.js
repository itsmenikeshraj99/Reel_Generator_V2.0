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
    },
  },
  plugins: [],
}
