import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { formatNumber } from '@/lib/utils';
import type { MetricResult } from '@/lib/types';

type Severity = 'high' | 'medium' | 'low';

const SEVERITY: Record<Severity, BadgeProps['variant']> = {
  high: 'destructive',
  medium: 'warning',
  low: 'muted',
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
              <TableCell className="text-right tabular-nums">
                {formatNumber(row.units)}
              </TableCell>
              <TableCell>
                <Badge variant={SEVERITY[row.severity]} className="capitalize">
                  {row.severity}
                </Badge>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
