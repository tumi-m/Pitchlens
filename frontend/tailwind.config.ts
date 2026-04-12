import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "#050A10",
        surface: "#0D1623",
        surfaceHover: "#162335",
        primary: "#4F8CF6",
        secondary: "#A1B0D8",
        accent: "#10B981",
        danger: "#EF4444",
      },
      fontFamily: {
        sans: ["Inter", "sans-serif"],
      },
      backgroundImage: {
        "gradient-radial": "radial-gradient(var(--tw-gradient-stops))",
      },
      borderRadius: {
        "4xl": "2rem",
      },
      boxShadow: {
        "glow-primary": "0 0 30px rgba(79, 140, 246, 0.3)",
        "glow-accent": "0 0 30px rgba(16, 185, 129, 0.3)",
        "glow-danger": "0 0 30px rgba(239, 68, 68, 0.3)",
        "glow-primary-lg": "0 0 60px rgba(79, 140, 246, 0.25)",
        "inner-glow": "inset 0 1px 0 rgba(255, 255, 255, 0.06)",
      },
      keyframes: {
        "fade-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        "slide-up": {
          "0%": { opacity: "0", transform: "translateY(24px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "pulse-glow": {
          "0%, 100%": { boxShadow: "0 0 20px rgba(79, 140, 246, 0.25)" },
          "50%": { boxShadow: "0 0 40px rgba(79, 140, 246, 0.5)" },
        },
        float: {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-12px)" },
        },
        "orb-drift": {
          "0%": { transform: "translate(0, 0) scale(1)" },
          "33%": { transform: "translate(30px, -20px) scale(1.05)" },
          "66%": { transform: "translate(-20px, 15px) scale(0.95)" },
          "100%": { transform: "translate(0, 0) scale(1)" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.6s ease-out forwards",
        "slide-up": "slide-up 0.6s ease-out forwards",
        "pulse-glow": "pulse-glow 2s ease-in-out infinite",
        float: "float 6s ease-in-out infinite",
        "orb-drift": "orb-drift 20s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
export default config;
