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
import type { AtRiskRow } from '@/lib/mock';

const SEVERITY: Record<AtRiskRow['severity'], BadgeProps['variant']> = {
  high: 'destructive',
  medium: 'warning',
  low: 'muted',
};

/** SKUs flagged by sell-through vs. lead time (docs/07 §4.2). */
export function InventoryAtRiskTable({
  rows,
  loading,
}: {
  rows?: AtRiskRow[];
  loading?: boolean;
}) {
  if (loading || !rows) {
    return <Skeleton className="h-64 w-full" />;
  }
  return (
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>SKU</TableHead>
            <TableHead>Product</TableHead>
            <TableHead>Category</TableHead>
            <TableHead className="text-right">Cover</TableHead>
            <TableHead className="text-right">Lead time</TableHead>
            <TableHead>Risk</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.sku}>
              <TableCell className="font-mono text-xs text-muted-foreground">
                {row.sku}
              </TableCell>
              <TableCell className="font-medium">{row.name}</TableCell>
              <TableCell className="text-muted-foreground">
                {row.category}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {row.days_of_cover}d
              </TableCell>
              <TableCell className="text-right tabular-nums text-muted-foreground">
                {row.lead_time_days}d
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
