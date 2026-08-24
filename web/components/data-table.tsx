import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { cn, formatCurrency, formatNumber, formatPercent } from '@/lib/utils';
import type { Cell, ColumnDtype, TableBlock } from '@/lib/types';

const NUMERIC: ColumnDtype[] = ['currency', 'number', 'ratio'];

function fmtCell(value: Cell, dtype: ColumnDtype): React.ReactNode {
  if (value == null) return <span className="text-muted-foreground">—</span>;
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'number') {
    if (dtype === 'currency') return formatCurrency(value);
    if (dtype === 'ratio') {
      const cls = value < 0 ? 'text-destructive' : value > 0 ? 'text-success' : '';
      const sign = value > 0 ? '+' : '';
      return (
        <span className={cls}>
          {sign}
          {formatPercent(value)}
        </span>
      );
    }
    return formatNumber(value, Number.isInteger(value) ? 0 : 2);
  }
  return value;
}

/** Renders a result `TableBlock` from the answer envelope. */
export function DataTable({ block }: { block: TableBlock }) {
  const columns = block.columns ?? [];
  const rows = block.rows ?? [];

  if (!columns.length) return null;

  return (
    <div>
      {block.name ? (
        <p className="mb-2 text-sm font-medium text-foreground">{block.name}</p>
      ) : null}
      <div className="scrollbar-thin overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              {columns.map((col, i) => (
                <TableHead
                  key={`${col.name}-${i}`}
                  scope="col"
                  className={NUMERIC.includes(col.dtype) ? 'text-right' : 'text-left'}
                >
                  {col.name}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.length ? (
              rows.map((row, ri) => (
                <TableRow key={ri}>
                  {columns.map((col, ci) => {
                    const numeric = NUMERIC.includes(col.dtype);
                    return (
                      <TableCell
                        key={ci}
                        className={cn(
                          numeric && 'text-right tabular-nums',
                          ci === 0 && 'font-medium',
                        )}
                      >
                        {fmtCell(row[ci] ?? null, col.dtype)}
                      </TableCell>
                    );
                  })}
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell
                  colSpan={columns.length}
                  className="text-center text-muted-foreground"
                >
                  No rows returned.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
