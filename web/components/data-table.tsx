'use client';

import * as React from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import { cn, formatCurrency, formatNumber, formatPercent } from '@/lib/utils';
import type { Cell, ColumnDtype, TableBlock } from '@/lib/types';

const NUMERIC: ColumnDtype[] = ['currency', 'number', 'ratio'];
const DEFAULT_PAGE_SIZE = 10;

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

/** Renders a result `TableBlock` with intelligent filtering, pagination, and collapsible support. */
export function DataTable({
  block,
  question,
  answer,
}: {
  block: TableBlock;
  question?: string;
  answer?: string;
}) {
  const allColumns = React.useMemo(() => block.columns ?? [], [block.columns]);
  const allRows = React.useMemo(() => block.rows ?? [], [block.rows]);
  const isAttachment = Boolean(block.name && /attached/i.test(block.name));

  // Determine if we can intelligently extract relevant rows and columns based on question/answer context
  const { filteredColumns, filteredRows, isFiltered } = React.useMemo(() => {
    if (!isAttachment || allRows.length <= 5) {
      return { filteredColumns: allColumns, filteredRows: allRows, isFiltered: false };
    }

    const context = `${question || ''} ${answer || ''}`.toLowerCase();

    // 1. Check for requested IDs/numbers mentioned in question (e.g., "1, 8, 23" or "order id 1")
    const idMatches = Array.from(context.matchAll(/\b(\d+)\b/g)).map((m) => m[1]);
    const idSet = new Set(idMatches);

    // Find if any column looks like an ID/identifier column
    const idColIndex = allColumns.findIndex((c) =>
      /id$|^id|_id/i.test(c.name)
    );

    let matchingRows: Cell[][] = [];
    if (idColIndex !== -1 && idSet.size > 0) {
      matchingRows = allRows.filter((r) => {
        const val = String(r[idColIndex] ?? '').trim();
        return idSet.has(val);
      });
    }

    // 2. Identify relevant columns (e.g. mentions of status, name, amount, or id)
    // Only filter columns if we matched specific rows
    let targetColumns = allColumns;
    let targetRows = allRows;

    if (matchingRows.length > 0) {
      // Find columns that are explicitly mentioned or key identifier/status columns
      const relevantColIndices: number[] = [];
      allColumns.forEach((col, idx) => {
        const cname = col.name.toLowerCase();
        // Always keep the ID column
        if (idx === idColIndex) {
          relevantColIndices.push(idx);
          return;
        }
        // Keep columns mentioned in question or answer
        const words = cname.split(/[\s_-]+/);
        if (words.some((w) => w.length > 2 && context.includes(w))) {
          relevantColIndices.push(idx);
        }
      });

      // If at least 1-4 columns match, filter to those; otherwise keep original columns
      if (relevantColIndices.length >= 2 && relevantColIndices.length < allColumns.length) {
        targetColumns = relevantColIndices
          .map((i) => allColumns[i])
          .filter((c): c is ColumnSpec => c !== undefined);
        targetRows = matchingRows.map((row) => relevantColIndices.map((i) => row[i] ?? null));
      } else {
        targetRows = matchingRows;
      }

      return {
        filteredColumns: targetColumns,
        filteredRows: targetRows,
        isFiltered: true,
      };
    }

    return { filteredColumns: allColumns, filteredRows: allRows, isFiltered: false };
  }, [allColumns, allRows, isAttachment, question, answer]);

  // Mode: show only relevant filtered rows or the full file
  const [viewAllRows, setViewAllRows] = React.useState(!isFiltered);
  const [isExpanded, setIsExpanded] = React.useState(true);
  const [page, setPage] = React.useState(1);

  const activeColumns = viewAllRows ? allColumns : filteredColumns;
  const activeRows = viewAllRows ? allRows : filteredRows;

  if (!allColumns.length) return null;

  const displayRows = viewAllRows ? activeRows.slice(0, page * DEFAULT_PAGE_SIZE) : activeRows;

  return (
    <div className="rounded-lg border bg-card/40 text-card-foreground">
      {block.name ? (
        <div className="flex flex-wrap items-center justify-between gap-2 border-b px-3.5 py-2.5">
          <button
            type="button"
            onClick={() => setIsExpanded((prev) => !prev)}
            className="flex items-center gap-1.5 text-xs font-medium text-foreground hover:text-primary transition-colors"
          >
            {isExpanded ? (
              <ChevronDown className="size-3.5 text-muted-foreground" />
            ) : (
              <ChevronRight className="size-3.5 text-muted-foreground" />
            )}
            <span>{block.name}</span>
            <span className="text-2xs text-muted-foreground font-normal">
              ({activeRows.length} {activeRows.length === 1 ? 'row' : 'rows'}
              {isFiltered && !viewAllRows ? ' relevant' : ''})
            </span>
          </button>

          <div className="flex items-center gap-2">
            {isFiltered ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setViewAllRows((prev) => !prev)}
                className="h-6 text-2xs px-2 text-primary"
              >
                {viewAllRows
                  ? `Show relevant rows only (${filteredRows.length})`
                  : `Show all rows & columns (${allRows.length})`}
              </Button>
            ) : null}

            {viewAllRows && activeRows.length > displayRows.length ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setPage((p) => p + 1)}
                className="h-6 text-2xs px-2"
              >
                Load more
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}

      {isExpanded ? (
        <div className="scrollbar-thin overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                {activeColumns.map((col, i) => (
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
              {displayRows.length ? (
                displayRows.map((row, ri) => (
                  <TableRow key={ri}>
                    {activeColumns.map((col, ci) => {
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
                    colSpan={activeColumns.length}
                    className="text-center text-muted-foreground"
                  >
                    No matching rows found.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      ) : null}
    </div>
  );
}
