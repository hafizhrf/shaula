/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#f4fbf5',
          100: '#e6f7ec',
          200: '#c2edd0',
          300: '#8edbb0',
          400: '#53c088',
          500: '#2e9e68',
          600: '#207f52',
          700: '#1c6543',
          800: '#1a5037',
          900: '#17422f',
        }
      }
    },
  },
  plugins: [],
}
