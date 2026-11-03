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
  // An absent value prints as an em dash, never as a confident 0 — the result
  // set is evidence, and inventing a zero would misreport it.
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

/**
 * Renders a result `TableBlock` from the answer envelope.
 *
 * This is the query's actual output, so the row count is stated: a reader who
 * can see "12 rows" knows whether the visible rows are the whole result or the
 * top of a scroll. The header sticks for the same reason — a long result stays
 * readable without losing which column is which.
 */
export function DataTable({ block }: { block: TableBlock }) {
  const columns = block.columns ?? [];
  const rows = block.rows ?? [];

  if (!columns.length) return null;

  return (
    <section>
      <div className="mb-1.5 flex flex-wrap items-baseline gap-x-2.5">
        {block.name ? (
          <h4 className="text-sm font-medium text-foreground">{block.name}</h4>
        ) : null}
        <span className="text-xs tabular-nums text-muted-foreground">
          {rows.length === 1 ? '1 row' : `${rows.length} rows`}
        </span>
      </div>
      <div className="scrollbar-thin max-h-[26rem] overflow-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              {columns.map((col, i) => (
                <TableHead
                  key={`${col.name}-${i}`}
                  scope="col"
                  className={cn(
                    // Sticky so a scrolled result never loses its column names.
                    'sticky top-0 z-10 bg-card',
                    NUMERIC.includes(col.dtype) ? 'text-right' : 'text-left',
                  )}
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
    </section>
  );
}
