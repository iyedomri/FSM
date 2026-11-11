/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: { extend: {
    colors: { obsidian: "#0B0C10", cyan: "#66FCF1", magenta: "#FF007F", teal: "#45A29E" },
    fontFamily: { display: ["Space Grotesk", "sans-serif"], sans: ["Inter", "sans-serif"] },
    boxShadow: { holo: "0 0 10px #66FCF126, inset 0 0 20px #66FCF105" },
    backgroundImage: { grid: "linear-gradient(#66FCF108 1px, transparent 1px), linear-gradient(90deg, #66FCF108 1px, transparent 1px)" }
  } },
  plugins: []
};
