import * as React from 'react';
import { cn } from '@/lib/utils';

export type InputProps = React.InputHTMLAttributes<HTMLInputElement>;

/**
 * Text input.
 *
 * Two states are styled off attributes rather than props so a caller does not
 * have to learn a second vocabulary:
 * - `aria-invalid` paints the destructive border (set it from your own
 *   validation state; it is what assistive tech reads anyway).
 * - `disabled` gets a filled, flattened surface. Opacity alone reads as
 *   "low contrast" rather than "not available".
 */
const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(
          'flex h-10 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm',
          'transition-[border-color,box-shadow] duration-150 ease-out',
          'file:border-0 file:bg-transparent file:text-sm file:font-medium',
          'placeholder:text-muted-foreground',
          'hover:border-border',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
          'forced-colors:focus-visible:outline forced-colors:focus-visible:outline-2',
          'aria-[invalid=true]:border-destructive aria-[invalid=true]:focus-visible:ring-destructive',
          'disabled:cursor-not-allowed disabled:border-border/60 disabled:bg-muted disabled:text-muted-foreground disabled:shadow-none disabled:placeholder:text-muted-foreground/60',
          // Figures a user compares (quantities, dates) should not jitter as
          // digits swap width while they type.
          '[&[type=date]]:tabular-nums [&[type=number]]:tabular-nums',
          className,
        )}
        ref={ref}
        {...props}
      />
    );
  },
);
Input.displayName = 'Input';

export { Input };
