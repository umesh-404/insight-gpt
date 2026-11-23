import * as React from 'react';
import { cn } from '@/lib/utils';

export interface LabelProps
  extends React.LabelHTMLAttributes<HTMLLabelElement> {
  /**
   * Marks the field as required. The asterisk is decorative — screen readers
   * get the word, because "star" is not a requirement.
   */
  required?: boolean;
  /**
   * Marks the field as optional. Say one or the other across a form, never
   * both: a form where some fields say "Optional" and others say nothing is
   * read as "the silent ones might be required too".
   */
  optional?: boolean;
}

const Label = React.forwardRef<HTMLLabelElement, LabelProps>(
  ({ className, required, optional, children, ...props }, ref) => (
    <label
      ref={ref}
      className={cn(
        'text-sm font-medium leading-none text-foreground',
        'peer-disabled:cursor-not-allowed peer-disabled:opacity-70',
        // Only becomes a flex row when there is a suffix to align — a plain
        // text label stays a plain block so existing layouts do not shift.
        (required || optional) && 'inline-flex items-center gap-1',
        className,
      )}
      {...props}
    >
      {children}
      {required ? (
        <>
          <span aria-hidden className="text-destructive">
            *
          </span>
          <span className="sr-only">(required)</span>
        </>
      ) : null}
      {optional ? (
        <span className="text-xs font-normal text-muted-foreground">
          (optional)
        </span>
      ) : null}
    </label>
  ),
);
Label.displayName = 'Label';

export { Label };
