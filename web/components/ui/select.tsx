import * as React from 'react';
import { ChevronDown } from 'lucide-react';
import { cn } from '@/lib/utils';

/**
 * A lightweight, accessible native `<select>` styled to match the design
 * system. (A full Radix listbox is overkill for the filter bars here, and the
 * native picker is the better control on a phone.)
 *
 * As with `Input`, `aria-invalid` and `disabled` are styled off the attribute
 * so there is no second vocabulary to learn.
 */
export interface SelectProps
  extends React.SelectHTMLAttributes<HTMLSelectElement> {
  options: Array<{ value: string; label: string }>;
  /**
   * Classes for the positioning wrapper rather than the control. Needed when
   * the select has to size itself inside a grid or flex row — `className`
   * lands on the `<select>` and cannot change the wrapper's own box.
   */
  containerClassName?: string;
}

const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, containerClassName, options, ...props }, ref) => (
    <div className={cn('relative', containerClassName)}>
      <select
        ref={ref}
        className={cn(
          'h-10 w-full appearance-none rounded-md border border-input bg-background px-3 pr-9 text-sm shadow-sm',
          'transition-[border-color,box-shadow] duration-150 ease-out',
          'hover:border-border',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
          'forced-colors:focus-visible:outline forced-colors:focus-visible:outline-2',
          'aria-[invalid=true]:border-destructive aria-[invalid=true]:focus-visible:ring-destructive',
          'disabled:cursor-not-allowed disabled:border-border/60 disabled:bg-muted disabled:text-muted-foreground disabled:shadow-none',
          className,
        )}
        {...props}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {/* `peer`-free: the chevron dims with the control via group-less
          attribute selectors on the wrapper's own child, so a disabled select
          does not keep a confident, fully-saturated arrow. */}
      <ChevronDown
        aria-hidden
        className={cn(
          'pointer-events-none absolute right-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground',
          props.disabled && 'opacity-50',
        )}
      />
    </div>
  ),
);
Select.displayName = 'Select';

export { Select };
