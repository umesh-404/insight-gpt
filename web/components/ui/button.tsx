import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { Loader2 } from 'lucide-react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const buttonVariants = cva(
  [
    'relative inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium',
    // Only the properties that actually change — never `all`. 150ms decelerating
    // keeps a press feeling instant on a laptop trackpad.
    'transition-[background-color,border-color,color,box-shadow,transform] duration-150 ease-out',
    // Press feedback. Subtle on purpose: enough to confirm the click landed,
    // not enough to read as a bounce on a control pressed all day.
    'active:scale-[0.98] active:transition-none',
    // The ring matches the global `:focus-visible` rule in globals.css rather
    // than competing with it. `forced-colors` drops box-shadows, so Windows
    // high-contrast mode gets a real outline instead of no focus ring at all.
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
    'forced-colors:focus-visible:outline forced-colors:focus-visible:outline-2',
    // Disabled reads as *inert*, not as *low contrast*: no shadow, no press,
    // no pointer. `aria-disabled` is the explained variant (see disabledReason)
    // and deliberately keeps pointer events so its tooltip can be read.
    'disabled:pointer-events-none disabled:opacity-60 disabled:shadow-none disabled:active:scale-100',
    'aria-disabled:cursor-not-allowed aria-disabled:opacity-60 aria-disabled:shadow-none aria-disabled:active:scale-100',
    '[&_svg]:size-4 [&_svg]:shrink-0',
    // A 36px control is comfortable with a mouse and too small with a thumb.
    // Grow every button to the 40px minimum only where the pointer is coarse,
    // so the desktop toolbars stay dense.
    '[@media(pointer:coarse)]:min-h-10 [@media(pointer:coarse)]:min-w-10',
  ],
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground shadow-sm hover:bg-primary/90',
        destructive:
          'bg-destructive text-destructive-foreground shadow-sm hover:bg-destructive/90',
        outline:
          'border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground hover:border-border',
        secondary:
          'bg-secondary text-secondary-foreground shadow-sm hover:bg-secondary/80',
        ghost: 'hover:bg-accent hover:text-accent-foreground',
        link: 'text-primary underline-offset-4 hover:underline',
      },
      size: {
        default: 'h-10 px-4 py-2',
        sm: 'h-9 rounded-md px-3',
        lg: 'h-11 rounded-md px-6 text-base',
        icon: 'size-10',
        /** Dense icon button for table rows and inline affordances. */
        'icon-sm': 'size-8 rounded-md [&_svg]:size-3.5',
        /** Dense text button for table rows and inline affordances. */
        xs: 'h-7 rounded-md px-2 text-xs [&_svg]:size-3.5',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
  /**
   * In-flight. Shows a spinner, sets `aria-busy`, and blocks activation —
   * without removing the button from the tab order, so focus does not jump
   * somewhere unexpected while a request is running.
   */
  loading?: boolean;
  /** Label to show while `loading`. Defaults to the normal children. */
  loadingLabel?: React.ReactNode;
  /**
   * Why this control cannot be used *right now*.
   *
   * A disabled control with no stated reason is the single most common UX
   * failure in this app: "Run now" is legitimately unavailable through a
   * pipeline's `queued` window (triggering again would 409), but a greyed
   * button alone just looks broken. Passing a reason switches the button from
   * the `disabled` attribute to `aria-disabled`, which keeps it focusable and
   * hoverable so the `title` can actually be read — a `disabled` element
   * receives no pointer events and therefore never shows a tooltip.
   *
   * Activation is blocked in JS, which `aria-disabled` alone does not do.
   */
  disabledReason?: string;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      className,
      variant,
      size,
      asChild = false,
      loading = false,
      loadingLabel,
      disabledReason,
      disabled,
      onClick,
      title,
      children,
      ...props
    },
    ref,
  ) => {
    const Comp = asChild ? Slot : 'button';
    const inert = Boolean(disabled) || loading;
    // Explained-inert: stay in the tree as a focusable, hoverable control.
    const explained = inert && Boolean(disabledReason);

    const handleClick = (event: React.MouseEvent<HTMLButtonElement>) => {
      if (inert) {
        // `aria-disabled` is advisory only — without this, Enter/Space and a
        // click both still fire (and a submit button would still submit).
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      onClick?.(event);
    };

    const label = loading && loadingLabel !== undefined ? loadingLabel : children;

    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        disabled={explained ? undefined : inert || undefined}
        aria-disabled={explained ? true : undefined}
        aria-busy={loading || undefined}
        title={explained ? disabledReason : title}
        // Only attach a handler when there is something to attach it for: a
        // Button rendered from a Server Component must not be handed a
        // function prop it never asked for.
        onClick={explained || onClick ? handleClick : undefined}
        {...props}
      >
        {/* Slot accepts exactly one child, so never wrap an `asChild` button. */}
        {asChild ? (
          children
        ) : (
          <>
            {loading ? (
              <Loader2 className="animate-spin" aria-hidden />
            ) : null}
            {label}
          </>
        )}
      </Comp>
    );
  },
);
Button.displayName = 'Button';

export { Button, buttonVariants };
