import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";

// PlugDrop design system tokens (see the PlugDrop design system: tokens.json).
const config: Config = {
  darkMode: ["class"],
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        display: ['"Bricolage Grotesque"', "serif"],
        sans: ["Manrope", "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "monospace"],
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      colors: {
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        card: { DEFAULT: "hsl(var(--card))", foreground: "hsl(var(--card-foreground))" },
        popover: { DEFAULT: "hsl(var(--popover))", foreground: "hsl(var(--popover-foreground))" },
        primary: { DEFAULT: "hsl(var(--primary))", foreground: "hsl(var(--primary-foreground))" },
        secondary: { DEFAULT: "hsl(var(--secondary))", foreground: "hsl(var(--secondary-foreground))" },
        muted: { DEFAULT: "hsl(var(--muted))", foreground: "hsl(var(--muted-foreground))" },
        accent: { DEFAULT: "hsl(var(--accent))", foreground: "hsl(var(--accent-foreground))" },
        destructive: { DEFAULT: "hsl(var(--destructive))", foreground: "hsl(var(--destructive-foreground))" },
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        pdpurple: { DEFAULT: "#8B5CF6", soft: "#A78BFA", deep: "#6D28D9", bg: "#F5F3FF" },
        pdblack: "#080808",
      },
      boxShadow: {
        panel: "0 10px 40px rgba(139, 92, 246, 0.12)",
        nav: "0 8px 30px rgba(139, 92, 246, 0.08)",
        "glow-sm": "0 0 20px rgba(139, 92, 246, 0.35)",
      },
    },
  },
  plugins: [animate],
};
export default config;
