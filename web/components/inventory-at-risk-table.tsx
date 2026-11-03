import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Skeleton } from '@/components/ui/skeleton';
import { cn, formatNumber } from '@/lib/utils';
import type { MetricResult } from '@/lib/types';

type Severity = 'high' | 'medium' | 'low';

/**
 * A dot plus the word, not a filled capsule per row.
 *
 * Eight rows of solid red and amber pills made the panel the loudest thing on
 * the dashboard regardless of what the numbers said, and the severity here is a
 * *relative* heuristic — it does not deserve more ink than the revenue trend.
 * Hue still distinguishes the levels, and the word is always beside it.
 */
const SEVERITY_DOT: Record<Severity, string> = {
  high: 'bg-destructive',
  medium: 'bg-warning',
  low: 'bg-muted-foreground/45',
};

interface AtRiskRow {
  product: string;
  units: number;
  severity: Severity;
}

/**
 * Severity heuristic: stock is ranked against the largest on-hand figure in the
 * returned set, so the badge reflects relative depletion rather than an
 * arbitrary absolute threshold.
 */
function toRows(result: MetricResult): AtRiskRow[] {
  const valueIndex = result.columns.findIndex(
    (c) => c.dtype === 'number' || c.dtype === 'currency',
  );
  const labelIndex = result.columns.findIndex((_, i) => i !== valueIndex);
  if (valueIndex < 0) return [];

  const rows = result.rows.map((row) => ({
    product: String(row[labelIndex] ?? '—'),
    units: Number(row[valueIndex] ?? 0),
  }));
  const max = rows.reduce((acc, r) => Math.max(acc, r.units), 0);

  return rows.map((row) => ({
    ...row,
    severity:
      max === 0 || row.units <= max * 0.25
        ? 'high'
        : row.units <= max * 0.6
          ? 'medium'
          : 'low',
  }));
}

/** Lowest-stock SKUs from the governed `units_on_hand` metric. */
export function InventoryAtRiskTable({
  result,
  loading,
}: {
  result?: MetricResult;
  loading?: boolean;
}) {
  if (loading) return <Skeleton className="h-64 w-full" />;

  const rows = result ? toRows(result) : [];
  // Shared denominator for the inline bars: depletion is only meaningful against
  // the deepest stock in the returned set, which is the same basis the severity
  // heuristic uses.
  const max = rows.reduce((acc, r) => Math.max(acc, r.units), 0);
  if (!rows.length) {
    return (
      <div className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">
        No inventory snapshot for the current filters.
      </div>
    );
  }

  return (
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Product</TableHead>
            <TableHead className="text-right">Units on hand</TableHead>
            <TableHead>Risk</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.product}>
              <TableCell className="font-medium">{row.product}</TableCell>
              <TableCell className="text-right">
                <span className="tabular-nums">{formatNumber(row.units)}</span>
                {/* One bar per row, one colour: the ranking is already in the
                    sort order, and the bar makes *how much* shallower each SKU
                    is readable from across a room. Aria-hidden — the number
                    beside it is the accessible value. */}
                <span
                  className="mt-1 block h-[3px] w-full overflow-hidden rounded-full bg-muted"
                  aria-hidden
                >
                  <span
                    className="block h-full rounded-full bg-chart-1"
                    style={{
                      width: `${max > 0 ? Math.max(2, (row.units / max) * 100) : 0}%`,
                    }}
                  />
                </span>
              </TableCell>
              <TableCell>
                <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                  <span
                    className={cn('size-2 shrink-0 rounded-full', SEVERITY_DOT[row.severity])}
                    aria-hidden
                  />
                  <span className="capitalize">{row.severity}</span>
                </span>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
