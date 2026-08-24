/**
 * Series colors come from the `--chart-*` design tokens (docs/07 §2.1), so every
 * chart — from /ask, a dashboard, or a report — shares one palette and themes
 * with the app. Recharts needs concrete color strings, so we reference the CSS
 * variables via `hsl(var(--chart-n))`; they resolve per theme at paint time.
 */
export const CHART_TOKENS = [
  'hsl(var(--chart-1))',
  'hsl(var(--chart-2))',
  'hsl(var(--chart-3))',
  'hsl(var(--chart-4))',
  'hsl(var(--chart-5))',
  'hsl(var(--chart-6))',
] as const;

export function seriesColor(index: number): string {
  return CHART_TOKENS[index % CHART_TOKENS.length]!;
}

/** Non-color markers so charts never rely on hue alone (a11y, docs/07 §7). */
export const SERIES_MARKERS = [
  'circle',
  'square',
  'triangle',
  'diamond',
  'cross',
  'star',
] as const;
