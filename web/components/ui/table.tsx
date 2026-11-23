import * as React from 'react';
import { cn } from '@/lib/utils';

export interface TableProps extends React.HTMLAttributes<HTMLTableElement> {
  /** Classes for the scroll container rather than the `<table>` itself. */
  containerClassName?: string;
  /**
   * Pin the header row while the body scrolls. Only useful when the container
   * is height-capped — pass that cap through `containerClassName`
   * (e.g. `max-h-[28rem] overflow-y-auto`).
   */
  stickyHeader?: boolean;
  /**
   * Names the horizontal scroll region and makes it keyboard-reachable.
   *
   * A wide table in an `overflow-x-auto` div is unreachable by keyboard: there
   * is nothing to focus, so the columns past the fold cannot be scrolled to
   * without a mouse. Giving the region a name is what lets us make it a tab
   * stop without adding an unlabelled one, so the tab stop only appears when a
   * caller supplies the label.
   */
  ariaLabel?: string;
}

const Table = React.forwardRef<HTMLTableElement, TableProps>(
  ({ className, containerClassName, stickyHeader, ariaLabel, ...props }, ref) => (
    <div
      role={ariaLabel ? 'region' : undefined}
      aria-label={ariaLabel}
      tabIndex={ariaLabel ? 0 : undefined}
      className={cn(
        'relative w-full overflow-x-auto scrollbar-thin',
        ariaLabel &&
          'rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
        containerClassName,
      )}
    >
      <table
        ref={ref}
        className={cn(
          // Tabular figures for the whole table, not just the columns someone
          // remembered to tag. Proportional digits make a column of ₹ figures
          // shear by a few pixels per row, which is exactly the thing an
          // evaluator reads as sloppy from the back of a lit room.
          'w-full caption-bottom text-sm tabular-nums',
          stickyHeader && [
            '[&_thead]:sticky [&_thead]:top-0 [&_thead]:z-10',
            // `border-collapse: collapse` (Tailwind's preflight default) drops
            // a sticky header's own border as it detaches, so the hairline is
            // painted as an inset shadow instead — which survives sticking.
            '[&_thead]:shadow-[inset_0_-1px_0_hsl(var(--border))] [&_thead_tr]:border-b-0',
          ],
          className,
        )}
        {...props}
      />
    </div>
  ),
);
Table.displayName = 'Table';

const TableHeader = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  // A tinted header keeps long result tables legible: without it the column
  // names read as just another row once the table scrolls.
  <thead
    ref={ref}
    className={cn('bg-elevated [&_tr]:border-b [&_tr]:hover:bg-transparent', className)}
    {...props}
  />
));
TableHeader.displayName = 'TableHeader';

const TableBody = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  <tbody
    ref={ref}
    className={cn('[&_tr:last-child]:border-0', className)}
    {...props}
  />
));
TableBody.displayName = 'TableBody';

/** Totals / summary row. Shares the header tint so it reads as chrome. */
const TableFooter = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  <tfoot
    ref={ref}
    className={cn(
      'border-t bg-elevated font-medium [&_tr]:border-0 [&_tr]:hover:bg-transparent',
      className,
    )}
    {...props}
  />
));
TableFooter.displayName = 'TableFooter';

const TableRow = React.forwardRef<
  HTMLTableRowElement,
  React.HTMLAttributes<HTMLTableRowElement>
>(({ className, ...props }, ref) => (
  <tr
    ref={ref}
    className={cn(
      'border-b transition-colors hover:bg-muted/50 data-[state=selected]:bg-muted',
      className,
    )}
    {...props}
  />
));
TableRow.displayName = 'TableRow';

export interface TableHeadProps
  extends React.ThHTMLAttributes<HTMLTableCellElement> {
  /**
   * Right-aligns the column and pins tabular figures, so the header sits over
   * the ones digit of the values beneath it. A numeric column labelled on the
   * left and filled on the right is the commonest misalignment in this app.
   */
  numeric?: boolean;
}

const TableHead = React.forwardRef<HTMLTableCellElement, TableHeadProps>(
  ({ className, numeric, ...props }, ref) => (
    <th
      ref={ref}
      className={cn(
        'h-10 whitespace-nowrap px-3 align-middle text-xs font-medium uppercase tracking-wide text-muted-foreground [&:has([role=checkbox])]:pr-0',
        numeric ? 'text-right' : 'text-left',
        className,
      )}
      {...props}
    />
  ),
);
TableHead.displayName = 'TableHead';

export interface TableCellProps
  extends React.TdHTMLAttributes<HTMLTableCellElement> {
  /** Right-aligned, tabular figures. Pair with `numeric` on the `TableHead`. */
  numeric?: boolean;
}

const TableCell = React.forwardRef<HTMLTableCellElement, TableCellProps>(
  ({ className, numeric, ...props }, ref) => (
    <td
      ref={ref}
      className={cn(
        'px-3 py-2.5 align-middle',
        numeric && 'text-right tabular-nums',
        className,
      )}
      {...props}
    />
  ),
);
TableCell.displayName = 'TableCell';

const TableCaption = React.forwardRef<
  HTMLTableCaptionElement,
  React.HTMLAttributes<HTMLTableCaptionElement>
>(({ className, ...props }, ref) => (
  <caption
    ref={ref}
    className={cn('mt-4 text-sm text-muted-foreground', className)}
    {...props}
  />
));
TableCaption.displayName = 'TableCaption';

export {
  Table,
  TableHeader,
  TableBody,
  TableFooter,
  TableHead,
  TableRow,
  TableCell,
  TableCaption,
};
