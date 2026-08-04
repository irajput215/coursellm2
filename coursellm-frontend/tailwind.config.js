/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'linkedin-blue': '#0a66c2',
        'linkedin-dark-blue': '#004182',
        'linkedin-bg': '#f3f2ef',
        'linkedin-gray': '#666666',
        'linkedin-text': '#191919',
        'linkedin-border': '#dce6f1',
        'linkedin-card': '#ffffff',
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
}
