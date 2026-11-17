/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Channel variables preserve Tailwind opacity modifiers.
        paper: "rgb(var(--rgb-paper) / <alpha-value>)",
        paper2: "rgb(var(--rgb-paper-2) / <alpha-value>)",
        paper3: "rgb(var(--rgb-paper-3) / <alpha-value>)",
        ink: "rgb(var(--rgb-ink) / <alpha-value>)",
        ink2: "rgb(var(--rgb-ink-2) / <alpha-value>)",
        muted: "rgb(var(--rgb-muted) / <alpha-value>)",
        brass: "rgb(var(--rgb-accent) / <alpha-value>)",
        brassink: "rgb(var(--rgb-accent-ink) / <alpha-value>)",
        coral: "rgb(var(--rgb-accent-2) / <alpha-value>)",
        ok: "rgb(var(--rgb-ok) / <alpha-value>)",
        warn: "rgb(var(--rgb-warn) / <alpha-value>)",
        danger: "rgb(var(--rgb-danger) / <alpha-value>)",
        background: "rgb(var(--rgb-paper) / <alpha-value>)",
        surface: "rgb(var(--rgb-paper-2) / <alpha-value>)",
        primary: "rgb(var(--rgb-accent) / <alpha-value>)",
        accent: "rgb(var(--rgb-accent-2) / <alpha-value>)",
      },
      fontFamily: {
        display: "var(--font-display)",
        body: "var(--font-body)",
        sans: "var(--font-body)",
        serif: "var(--font-display)",
        mono: "var(--font-mono)",
      },
      borderColor: {
        rule: "var(--color-rule)",
        rule2: "var(--color-rule-2)",
      },
      borderRadius: {
        card: "var(--radius-card)",
        input: "var(--radius-input)",
      },
      fontSize: {
        micro: ["12px", { letterSpacing: "0.025em" }],
      },
      transitionTimingFunction: {
        out: "var(--ease-out)",
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'fade': 'fadeIn 0.4s var(--ease-out)',
        // mobile shell: the nav drawer flies in from the edge, sheets rise
        'slide-in-left': 'slideInLeft 0.24s var(--ease-out)',
        'sheet-up': 'sheetUp 0.26s var(--ease-out)',
      },
      keyframes: {
        fadeIn: {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        slideInLeft: {
          from: { transform: 'translateX(-100%)' },
          to: { transform: 'translateX(0)' },
        },
        sheetUp: {
          from: { transform: 'translateY(12px)', opacity: '0' },
          to: { transform: 'translateY(0)', opacity: '1' },
        },
      },
    },
  },
  plugins: [],
}
