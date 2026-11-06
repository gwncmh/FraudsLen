import animate from "tailwindcss-animate";

/** Theme theo chuẩn shadcn/ui (màu lấy từ biến CSS trong src/index.css). */
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  // màu mức rủi ro được ghép động (bg-${tone})
  safelist: ["bg-critical", "bg-high", "bg-medium", "bg-low", "bg-unscored", "bg-primary"],
  theme: {
    container: { center: true, padding: "2rem" },
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: { DEFAULT: "hsl(var(--primary))", foreground: "hsl(var(--primary-foreground))" },
        secondary: { DEFAULT: "hsl(var(--secondary))", foreground: "hsl(var(--secondary-foreground))" },
        destructive: { DEFAULT: "hsl(var(--destructive))", foreground: "hsl(var(--destructive-foreground))" },
        muted: { DEFAULT: "hsl(var(--muted))", foreground: "hsl(var(--muted-foreground))" },
        accent: { DEFAULT: "hsl(var(--accent))", foreground: "hsl(var(--accent-foreground))" },
        popover: { DEFAULT: "hsl(var(--popover))", foreground: "hsl(var(--popover-foreground))" },
        card: { DEFAULT: "hsl(var(--card))", foreground: "hsl(var(--card-foreground))" },
        sidebar: { DEFAULT: "hsl(var(--sidebar))", foreground: "hsl(var(--sidebar-foreground))", border: "hsl(var(--sidebar-border))" },
        // mức rủi ro
        critical: "hsl(var(--critical))",
        high: "hsl(var(--high))",
        medium: "hsl(var(--medium))",
        low: "hsl(var(--low))",
        unscored: "hsl(var(--unscored))",
      },
      borderRadius: { lg: "var(--radius)", md: "calc(var(--radius) - 2px)", sm: "calc(var(--radius) - 4px)" },
      fontFamily: { sans: ["Inter", "Segoe UI", "system-ui", "sans-serif"], mono: ["JetBrains Mono", "Consolas", "monospace"] },
      keyframes: {
        "pulse-ring": { "0%": { transform: "scale(.8)", opacity: ".8" }, "100%": { transform: "scale(2.2)", opacity: "0" } },
      },
      animation: { "pulse-ring": "pulse-ring 1.6s cubic-bezier(.2,.6,.4,1) infinite" },
    },
  },
  plugins: [animate],
};
