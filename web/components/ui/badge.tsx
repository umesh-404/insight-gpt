import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

/**
 * A small, non-interactive chip.
 *
 * `Badge` is deliberately *vocabulary-free*: it knows about tone, not meaning.
 * Picking the tone for a domain status is the job of `StatusBadge` /
 * `ReportStatusBadge` in `components/status-badge.tsx`, because the mapping is
 * where the bugs live (a report badged "Success" was a real shipped defect).
 * Reach for a raw `Badge` for labels and counts, not for statuses.
 */
const badgeVariants = cva(
  [
    'inline-flex items-center gap-1 rounded-full border font-medium tabular-nums',
    'transition-colors',
    // Not focusable by default; styled so it still reads correctly if a caller
    // wraps it in something that is.
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
    // The tinted fills below are system colours in Windows high-contrast mode,
    // i.e. invisible. A real border is what keeps the chip a chip there.
    'forced-colors:border-[ButtonBorder] forced-colors:text-[ButtonText]',
    '[&>svg]:size-3 [&>svg]:shrink-0',
  ],
  {
    variants: {
      variant: {
        default: 'border-transparent bg-primary/10 text-primary',
        secondary: 'border-transparent bg-secondary text-secondary-foreground',
        outline: 'border-border text-foreground',
        success: 'border-transparent bg-success/12 text-success',
        warning: 'border-transparent bg-warning/15 text-warning',
        destructive: 'border-transparent bg-destructive/12 text-destructive',
        muted: 'border-transparent bg-muted text-muted-foreground',
      },
      size: {
        default: 'px-2 py-0.5 text-xs',
        /** For a chip riding inside a dense table row. */
        sm: 'gap-0.5 px-1.5 py-0 text-2xs [&>svg]:size-2.5',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, size, ...props }: BadgeProps) {
  return (
    <span className={cn(badgeVariants({ variant, size }), className)} {...props} />
  );
}

export { Badge, badgeVariants };
