'use client';

import { CalendarDays, SlidersHorizontal } from 'lucide-react';
import { Select } from '@/components/ui/select';

export interface DashboardFilters {
  range: '7d' | '30d' | 'quarter' | 'ytd';
  region: string;
  category: string;
}

const RANGE_OPTIONS = [
  { value: '7d', label: 'Last 7 days' },
  { value: '30d', label: 'Last 30 days' },
  { value: 'quarter', label: 'This quarter' },
  { value: 'ytd', label: 'Year to date' },
];

const REGION_OPTIONS = [
  { value: 'all', label: 'All regions' },
  { value: 'north', label: 'North' },
  { value: 'south', label: 'South' },
  { value: 'east', label: 'East' },
  { value: 'west', label: 'West' },
];

const CATEGORY_OPTIONS = [
  { value: 'all', label: 'All categories' },
  { value: 'electronics', label: 'Electronics' },
  { value: 'home', label: 'Home & Kitchen' },
  { value: 'outdoor', label: 'Outdoor & Garden' },
  { value: 'apparel', label: 'Apparel' },
];

/**
 * Range + dimension filter bar. Each change should re-issue POST /metrics/query
 * so tiles and charts stay consistent (docs/07 §4.2).
 */
export function DateRangeFilter({
  value,
  onChange,
}: {
  value: DashboardFilters;
  onChange: (next: DashboardFilters) => void;
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
            onChange({ ...value, range: e.target.value as DashboardFilters['range'] })
          }
        />
      </div>
      <div className="ml-auto flex items-center gap-1.5 text-muted-foreground">
        <SlidersHorizontal className="size-4" aria-hidden />
        <span className="text-xs font-medium">Filters</span>
      </div>
      <div className="w-40">
        <Select
          aria-label="Region"
          options={REGION_OPTIONS}
          value={value.region}
          onChange={(e) => onChange({ ...value, region: e.target.value })}
        />
      </div>
      <div className="w-44">
        <Select
          aria-label="Category"
          options={CATEGORY_OPTIONS}
          value={value.category}
          onChange={(e) => onChange({ ...value, category: e.target.value })}
        />
      </div>
    </div>
  );
}
