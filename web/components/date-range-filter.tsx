'use client';

import { CalendarDays, SlidersHorizontal, X } from 'lucide-react';
import { Select } from '@/components/ui/select';
import { RANGE_OPTIONS, type RangeKey } from '@/lib/metrics';

export interface DashboardFilters {
  range: RangeKey;
  /** `all` means "no predicate"; other values are governed dimension values. */
  region: string;
  category: string;
}

/**
 * Lower-cased plural of a dimension label from the catalog. Naive `+ "s"`
 * produced "All categorys" on screen; the -y rule covers every label the
 * catalog actually ships, and anything already plural is left alone.
 */
function plural(label: string): string {
  const word = label.toLowerCase();
  if (word.endsWith('s')) return word;
  if (/[^aeiou]y$/.test(word)) return `${word.slice(0, -1)}ies`;
  if (/(ch|sh|x|z)$/.test(word)) return `${word}es`;
  return `${word}s`;
}

export const ALL_VALUES = 'all';

function withAll(
  values: string[],
  allLabel: string,
): Array<{ value: string; label: string }> {
  return [
    { value: ALL_VALUES, label: allLabel },
    ...values.map((value) => ({ value, label: value })),
  ];
}

/**
 * Range + dimension filter bar. Every change re-issues POST /metrics/query so
 * tiles and charts stay consistent.
 *
 * Dimension values are supplied by the caller from a governed query rather than
 * hard-coded, so the options always match what the warehouse actually contains
 * (including their exact casing, which the filter predicate is sensitive to).
 */
export function DateRangeFilter({
  value,
  onChange,
  regions,
  categories,
  regionLabel = 'Region',
  categoryLabel = 'Category',
  loading,
  resolvedNote,
}: {
  value: DashboardFilters;
  onChange: (next: DashboardFilters) => void;
  regions: string[];
  categories: string[];
  /** Human labels from the metric catalog, falling back to the dimension id. */
  regionLabel?: string;
  categoryLabel?: string;
  loading?: boolean;
  /**
   * What the selected range actually resolved to, plus how far the warehouse
   * runs. It lives inside the control that caused it: a relative range resolved
   * against warehouse coverage rather than the wall clock is surprising, and the
   * answer belongs next to the question, not in a caption further down the page.
   */
  resolvedNote?: string | null;
}) {
  return (
    <div className="rounded-lg border bg-card shadow-soft">
      <div className="flex flex-wrap items-center gap-2 p-2">
        <div className="flex items-center gap-1.5 pl-1 text-muted-foreground">
          <CalendarDays className="size-4" aria-hidden />
          <span className="text-xs font-medium">Range</span>
        </div>
        <div className="min-w-[9rem] flex-1 sm:w-40 sm:flex-none">
          <Select
            aria-label="Date range"
            options={RANGE_OPTIONS}
            value={value.range}
            onChange={(e) =>
              onChange({ ...value, range: e.target.value as RangeKey })
            }
          />
        </div>
        <div className="flex items-center gap-1.5 text-muted-foreground sm:ml-auto">
          <SlidersHorizontal className="size-4" aria-hidden />
          <span className="text-xs font-medium">Filters</span>
        </div>
        <div className="min-w-[9rem] flex-1 sm:w-40 sm:flex-none">
          <Select
            aria-label={regionLabel}
            disabled={loading || regions.length === 0}
            options={withAll(regions, `All ${plural(regionLabel)}`)}
            value={value.region}
            onChange={(e) => onChange({ ...value, region: e.target.value })}
          />
        </div>
        <div className="min-w-[9rem] flex-1 sm:w-44 sm:flex-none">
          <Select
            aria-label={categoryLabel}
            disabled={loading || categories.length === 0}
            options={withAll(categories, `All ${plural(categoryLabel)}`)}
            value={value.category}
            onChange={(e) => onChange({ ...value, category: e.target.value })}
          />
        </div>
        {/* Only offered once a predicate is actually narrowing the page — a
            permanent "Clear" implies a filter is applied when none is. */}
        {value.region !== ALL_VALUES || value.category !== ALL_VALUES ? (
          <button
            type="button"
            onClick={() =>
              onChange({ ...value, region: ALL_VALUES, category: ALL_VALUES })
            }
            className="inline-flex min-h-10 items-center gap-1 rounded-md px-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <X className="size-3.5" aria-hidden />
            Clear filters
          </button>
        ) : null}
      </div>

      {resolvedNote ? (
        <p className="border-t px-3 py-2 text-xs tabular-nums text-muted-foreground">
          {resolvedNote}
        </p>
      ) : null}
    </div>
  );
}
