/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        sidebar: '#0f1116',
        accent: '#f59e0b',
      }
    },
  },
  plugins: [
    require('@tailwindcss/forms'),
  ],
}
