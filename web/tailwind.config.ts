import type { Config } from 'tailwindcss';

/**
 * Tailwind is driven entirely by the semantic CSS variables declared in
 * `app/globals.css` (see docs/07-frontend.md §2). Components consume tokens
 * (`bg-card`, `text-muted-foreground`, `stroke-chart-1`) and never hardcode hex.
 */
const config: Config = {
  darkMode: ['class'],
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './lib/**/*.{ts,tsx}',
  ],
  theme: {
    container: {
      center: true,
      padding: '1.5rem',
      screens: {
        '2xl': '1400px',
      },
    },
    extend: {
      colors: {
        /* `border-border` stays the decorative hairline; `border-border-strong`
           is the rule that carries structure and has to survive a projector. */
        border: {
          DEFAULT: 'hsl(var(--border))',
          strong: 'hsl(var(--border-strong))',
        },
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
          /* Opaque selection tint. Prefer it over `bg-primary/10`, whose
             rendered value (and therefore its contrast) depends on whichever
             surface happens to be underneath. */
          subtle: 'hsl(var(--primary-subtle))',
          'subtle-foreground': 'hsl(var(--primary-subtle-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))',
        },
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },
        /* One step above `card`: nested panels, table headers, code chrome. */
        elevated: 'hsl(var(--elevated))',
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))',
        },
        /* `*-subtle` is the status *surface*; the base token is the status text
           that sits on it. Both directions were contrast-checked. */
        success: {
          DEFAULT: 'hsl(var(--success))',
          foreground: 'hsl(var(--success-foreground))',
          subtle: 'hsl(var(--success-subtle))',
        },
        warning: {
          DEFAULT: 'hsl(var(--warning))',
          foreground: 'hsl(var(--warning-foreground))',
          subtle: 'hsl(var(--warning-subtle))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
          subtle: 'hsl(var(--destructive-subtle))',
        },
        chart: {
          1: 'hsl(var(--chart-1))',
          2: 'hsl(var(--chart-2))',
          3: 'hsl(var(--chart-3))',
          4: 'hsl(var(--chart-4))',
          5: 'hsl(var(--chart-5))',
          6: 'hsl(var(--chart-6))',
          positive: 'hsl(var(--chart-positive))',
          negative: 'hsl(var(--chart-negative))',
          /* Chart chrome, so a Recharts <CartesianGrid>/<XAxis> never has to
             reach for a generic `border` and end up invisible on a projector. */
          grid: 'hsl(var(--chart-grid))',
          axis: 'hsl(var(--chart-axis))',
        },
      },
      borderRadius: {
        xl: 'calc(var(--radius) + 4px)',
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
      fontSize: {
        /*
         * A ~1.20 scale, now anchored at 15px body (was 14px).
         *
         * Every step moved up by one notch. The old scale was tuned for a
         * laptop at arm's length, and this app is also read off a projector
         * across a room, where 13px secondary text (`text-sm`, which carries
         * most of the labels, units and table cells here) stops being read and
         * starts being guessed at. The whole ladder moved together so the
         * ratios — and therefore the hierarchy — are unchanged; nothing
         * shrank, and no key was added or removed.
         *
         * Density is protected the way the design direction says to protect
         * it: by deleting chrome, not by shrinking type.
         */
        '2xs': ['0.75rem', { lineHeight: '1.0625rem', letterSpacing: '0.01em' }],
        xs: ['0.8125rem', { lineHeight: '1.1875rem' }],
        sm: ['0.875rem', { lineHeight: '1.4375rem' }],
        base: ['0.9375rem', { lineHeight: '1.5625rem' }],
        lg: ['1.0625rem', { lineHeight: '1.6875rem' }],
        xl: ['1.1875rem', { lineHeight: '1.8125rem' }],
        '2xl': ['1.5rem', { lineHeight: '2rem', letterSpacing: '-0.014em' }],
        '3xl': ['1.875rem', { lineHeight: '2.25rem', letterSpacing: '-0.019em' }],
        '4xl': ['2.25rem', { lineHeight: '2.625rem', letterSpacing: '-0.022em' }],
      },
      fontFamily: {
        sans: ['var(--font-sans)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['var(--font-mono)', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      boxShadow: {
        soft: 'var(--shadow-soft)',
        raised: 'var(--shadow-raised)',
        overlay: 'var(--shadow-overlay)',
      },
      transitionTimingFunction: {
        /* Decelerating ease — motion arrives and settles, never bounces. */
        out: 'cubic-bezier(0.16, 1, 0.3, 1)',
      },
      keyframes: {
        'accordion-down': {
          from: { height: '0' },
          to: { height: 'var(--radix-accordion-content-height)' },
        },
        'accordion-up': {
          from: { height: 'var(--radix-accordion-content-height)' },
          to: { height: '0' },
        },
        'fade-in': {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'caret-blink': {
          '0%,70%,100%': { opacity: '1' },
          '20%,50%': { opacity: '0' },
        },
        /* Skeleton sheen — reads as "work in progress", unlike a flat pulse. */
        shimmer: {
          '100%': { transform: 'translateX(100%)' },
        },
        /* Three dots that rise in sequence while the engine is still routing. */
        'thinking-dot': {
          '0%,80%,100%': { opacity: '0.25', transform: 'translateY(0)' },
          '40%': { opacity: '1', transform: 'translateY(-2px)' },
        },
        /* Indeterminate progress sweep for the streaming header. */
        sweep: {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(300%)' },
        },
      },
      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out',
        'fade-in': 'fade-in 0.3s cubic-bezier(0.16, 1, 0.3, 1)',
        'caret-blink': 'caret-blink 1s ease-out infinite',
        shimmer: 'shimmer 1.6s infinite',
        'thinking-dot': 'thinking-dot 1.2s ease-in-out infinite',
        sweep: 'sweep 1.4s cubic-bezier(0.4, 0, 0.2, 1) infinite',
      },
    },
  },
  plugins: [require('tailwindcss-animate')],
};

export default config;
