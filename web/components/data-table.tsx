import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { cn, formatCurrency, formatNumber, formatPercent } from '@/lib/utils';
import type { TableBlock, ColumnDtype } from '@/lib/types';

function fmtCell(value: string | number | null, dtype: ColumnDtype): React.ReactNode {
  if (value == null) return <span className="text-muted-foreground">—</span>;
  if (typeof value === 'number') {
    if (dtype === 'currency') return formatCurrency(value);
    if (dtype === 'ratio') {
      const cls =
        value < 0 ? 'text-destructive' : value > 0 ? 'text-success' : '';
      const sign = value > 0 ? '+' : '';
      return <span className={cls}>{sign}{formatPercent(value)}</span>;
    }
    return formatNumber(value, Number.isInteger(value) ? 0 : 2);
  }
  return value;
}

/** Renders a result `TableBlock` from the answer envelope. */
export function DataTable({ block }: { block: TableBlock }) {
  return (
    <div>
      {block.name ? (
        <p className="mb-2 text-sm font-medium text-foreground">{block.name}</p>
      ) : null}
      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              {block.columns.map((col) => (
                <TableHead
                  key={col.name}
                  className={cn(
                    col.dtype === 'currency' ||
                      col.dtype === 'number' ||
                      col.dtype === 'ratio'
                      ? 'text-right'
                      : 'text-left',
                  )}
                >
                  {col.name}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {block.rows.map((row, ri) => (
              <TableRow key={ri}>
                {row.map((cell, ci) => {
                  const dtype = block.columns[ci]?.dtype ?? 'string';
                  const numeric =
                    dtype === 'currency' || dtype === 'number' || dtype === 'ratio';
                  return (
                    <TableCell
                      key={ci}
                      className={cn(
                        numeric && 'text-right tabular-nums',
                        ci === 0 && 'font-medium',
                      )}
                    >
                      {fmtCell(cell, dtype)}
                    </TableCell>
                  );
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
