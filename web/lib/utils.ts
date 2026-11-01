import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** Merge conditional class names, resolving Tailwind conflicts predictably. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/**
 * Money and counts are rendered on the Indian scale: lakh/crore grouping and
 * the rupee symbol. Kept as one constant so a figure can never appear in two
 * conventions on the same screen.
 */
const MONEY_LOCALE = 'en-IN';

/**
 * Format a number as currency, compacting large values.
 *
 * Compact form keeps **three significant digits** rather than zero decimals.
 * With whole-unit compaction, 13,00,000 and 11,52,000 would both render "₹1Cr",
 * so a before/after pair reads "₹1Cr → ₹1Cr" and the change vanishes from a
 * screen whose entire job is to show it. Three significant digits gives
 * "₹13L → ₹11.5L" while keeping round values clean ("₹4.33L", not "₹4.3333L").
 *
 * The threshold uses the magnitude, so large negatives compact too — a delta of
 * -1,30,000 must not render as "-₹1,30,000" beside a compacted positive.
 */
export function formatCurrency(
  value: number,
  currency = 'INR',
  maximumFractionDigits = 0,
): string {
  // 1,00,000 is where the Indian scale switches to lakh, which is exactly the
  // threshold at which compacting starts paying for itself.
  const compact = Math.abs(value) >= 100_000;
  return new Intl.NumberFormat(MONEY_LOCALE, {
    style: 'currency',
    currency,
    ...(compact
      ? { notation: 'compact' as const, maximumSignificantDigits: 3 }
      : { notation: 'standard' as const, maximumFractionDigits }),
  }).format(value);
}

/** Format a plain number with grouping. */
export function formatNumber(value: number, maximumFractionDigits = 0): string {
  return new Intl.NumberFormat(MONEY_LOCALE, { maximumFractionDigits }).format(value);
}

/** Format a 0..1 ratio as a percentage string. */
export function formatPercent(value: number, fractionDigits = 1): string {
  return `${(value * 100).toFixed(fractionDigits)}%`;
}

/** Human-readable duration from milliseconds (e.g. "1m 12s", "820ms"). */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return '—';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const totalSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes === 0) return `${seconds}s`;
  return `${minutes}m ${seconds.toString().padStart(2, '0')}s`;
}

/**
 * Format an ISO timestamp as a short date-time.
 *
 * Pinned to UTC on purpose: these strings are produced during SSR *and* during
 * hydration, and a server/browser timezone difference would otherwise trip a
 * React hydration mismatch on every timestamp on the page.
 */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return `${new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    timeZone: 'UTC',
  }).format(date)} UTC`;
}

/** Format an ISO date (no time component) — used for citation dates. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  }).format(date);
}

/**
 * Relative "time ago" for run tables and conversation lists.
 *
 * Depends on `Date.now()`, so it is **not** hydration-safe on its own — render
 * it through `<RelativeTime>`, which only switches to relative text after mount.
 */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';
  const diff = Date.now() - then;
  const minutes = Math.round(diff / 60_000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  return formatDateTime(iso);
}

/**
 * Build a tick formatter that keeps ONE unit across a whole axis.
 *
 * `formatCurrency` compacts per value at the lakh threshold, which is right
 * for a figure in prose and wrong for an axis: ticks of 0 / 50k / 1L / 1.5L /
 * 2L rendered "₹0, ₹50,000, ₹1L, ₹1.5L, ₹2L", two units on one scale, so the
 * reader re-anchors at every gridline. The unit is picked once from `max` -
 * the largest value actually plotted - and applied to every tick, which also
 * keeps labels short enough that a fixed-width axis cannot shear their
 * leading digits off.
 *
 * Zero is always bare ("₹0", not "₹0Cr"): a scaled zero is still zero, and the
 * suffix only adds noise at the axis origin.
 */
export function makeAxisTickFormatter(
  max: number,
  format?: string | null,
): (value: number) => string {
  const magnitude = Math.abs(max);
  const { divisor, suffix } =
    magnitude >= 1e7
      ? { divisor: 1e7, suffix: 'Cr' }
      : magnitude >= 1e5
        ? { divisor: 1e5, suffix: 'L' }
        : { divisor: 1, suffix: '' };

  return (value: number) => {
    if (format === 'ratio' || format === 'percent') return formatPercent(value, 0);
    const currency = format === 'currency';
    if (value === 0) return currency ? formatCurrency(0) : '0';
    if (!suffix) return currency ? formatCurrency(value) : formatNumber(value);
    const scaled = value / divisor;
    const text = Number.isInteger(scaled)
      ? String(scaled)
      : scaled.toFixed(1).replace(/\.0$/, '');
    return currency ? `₹${text}${suffix}` : `${text}${suffix}`;
  };
}
