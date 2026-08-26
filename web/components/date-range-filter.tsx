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
}: {
  value: DashboardFilters;
  onChange: (next: DashboardFilters) => void;
  regions: string[];
  categories: string[];
  /** Human labels from the metric catalog, falling back to the dimension id. */
  regionLabel?: string;
  categoryLabel?: string;
  loading?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-card p-2 shadow-soft">
      <div className="flex items-center gap-1.5 pl-1 text-muted-foreground">
        <CalendarDays className="size-4" aria-hidden />
        <span className="text-xs font-medium">Range</span>
      </div>
      <div className="w-40">
        <Select
          aria-label="Date range"
          options={RANGE_OPTIONS}
          value={value.range}
          onChange={(e) =>
            onChange({ ...value, range: e.target.value as RangeKey })
          }
        />
      </div>
      <div className="ml-auto flex items-center gap-1.5 text-muted-foreground">
        <SlidersHorizontal className="size-4" aria-hidden />
        <span className="text-xs font-medium">Filters</span>
      </div>
      <div className="w-40">
        <Select
          aria-label={regionLabel}
          disabled={loading || regions.length === 0}
          options={withAll(regions, `All ${regionLabel.toLowerCase()}s`)}
          value={value.region}
          onChange={(e) => onChange({ ...value, region: e.target.value })}
        />
      </div>
      <div className="w-44">
        <Select
          aria-label={categoryLabel}
          disabled={loading || categories.length === 0}
          options={withAll(categories, `All ${categoryLabel.toLowerCase()}s`)}
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
          className="inline-flex items-center gap-1 rounded-md px-2 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          <X className="size-3.5" aria-hidden />
          Clear filters
        </button>
      ) : null}
    </div>
  );
}
