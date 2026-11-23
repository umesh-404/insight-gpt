import * as React from 'react';
import { cn } from '@/lib/utils';

export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  /**
   * Lift the card on hover. Reserve it for cards that *do* something when
   * clicked or expanded — a static panel that lifts is a false affordance.
   */
  interactive?: boolean;
  /**
   * How much chrome the surface carries.
   *
   * - `default` — border + soft shadow. A top-level panel on the page.
   * - `flat` — border only. For a panel that already sits on a card: a second
   *   shadow reads as a stack of boxes rather than a hierarchy.
   * - `bare` — no border, no shadow, no background. Spacing and nothing else.
   *   **Use this instead of putting a Card inside a Card.** The house direction
   *   is hairlines and whitespace, not nested boxes; `bare` + `CardSection`
   *   gives you the structure without the second frame.
   */
  variant?: 'default' | 'flat' | 'bare';
}

const Card = React.forwardRef<HTMLDivElement, CardProps>(
  ({ className, interactive, variant = 'default', ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        'rounded-lg text-card-foreground',
        variant === 'default' && 'border bg-card shadow-soft',
        variant === 'flat' && 'border bg-card',
        // No background either: a `bare` card must not create a second tint
        // step when it is laid over `card` or `elevated`.
        variant === 'bare' && 'bg-transparent',
        interactive &&
          'transition-[box-shadow,border-color,transform] duration-200 ease-out hover:-translate-y-px hover:border-border/80 hover:shadow-raised',
        className,
      )}
      {...props}
    />
  ),
);
Card.displayName = 'Card';

export interface CardHeaderProps extends React.HTMLAttributes<HTMLDivElement> {
  /**
   * Close the header with a hairline instead of relying on the gap alone.
   * This is what lets a long panel read as sections without nesting a card
   * per section.
   */
  divided?: boolean;
  /** Tighter padding for dense panels and side rails. */
  compact?: boolean;
}

const CardHeader = React.forwardRef<HTMLDivElement, CardHeaderProps>(
  ({ className, divided, compact, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        'flex flex-col space-y-1.5',
        compact ? 'p-4' : 'p-6',
        divided && (compact ? 'mb-4 border-b pb-3' : 'mb-6 border-b pb-4'),
        className,
      )}
      {...props}
    />
  ),
);
CardHeader.displayName = 'CardHeader';

export interface CardTitleProps
  extends React.HTMLAttributes<HTMLHeadingElement> {
  /**
   * The heading level this title actually occupies on the page. Visual weight
   * and document outline are separate problems; a panel title inside an `h1`
   * page is usually an `h2`, and defaulting every card to `h3` quietly breaks
   * the outline screen-reader users navigate by.
   */
  as?: 'h2' | 'h3' | 'h4' | 'div';
}

const CardTitle = React.forwardRef<HTMLHeadingElement, CardTitleProps>(
  ({ className, as: Comp = 'h3', ...props }, ref) => (
    <Comp
      ref={ref}
      className={cn(
        'text-base font-semibold leading-none tracking-tight',
        className,
      )}
      {...props}
    />
  ),
);
CardTitle.displayName = 'CardTitle';

const CardDescription = React.forwardRef<
  HTMLParagraphElement,
  React.HTMLAttributes<HTMLParagraphElement>
>(({ className, ...props }, ref) => (
  <p
    ref={ref}
    className={cn('text-sm text-muted-foreground', className)}
    {...props}
  />
));
CardDescription.displayName = 'CardDescription';

export interface CardContentProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Tighter padding for dense panels and side rails. */
  compact?: boolean;
  /** Drop the padding entirely — for a full-bleed table or chart. */
  flush?: boolean;
}

const CardContent = React.forwardRef<HTMLDivElement, CardContentProps>(
  ({ className, compact, flush, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        flush ? 'p-0' : compact ? 'p-4 pt-0' : 'p-6 pt-0',
        className,
      )}
      {...props}
    />
  ),
);
CardContent.displayName = 'CardContent';

export interface CardFooterProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Separate the footer from the body with a hairline. */
  divided?: boolean;
  /** Tighter padding for dense panels and side rails. */
  compact?: boolean;
}

const CardFooter = React.forwardRef<HTMLDivElement, CardFooterProps>(
  ({ className, divided, compact, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        'flex items-center',
        compact ? 'p-4 pt-0' : 'p-6 pt-0',
        divided && (compact ? 'mt-4 border-t pt-4' : 'mt-6 border-t pt-4'),
        className,
      )}
      {...props}
    />
  ),
);
CardFooter.displayName = 'CardFooter';

export interface CardSectionProps
  extends Omit<React.HTMLAttributes<HTMLDivElement>, 'title'> {
  /** Optional section heading, rendered on the hairline. */
  title?: React.ReactNode;
  /** One line of context under the heading. */
  description?: React.ReactNode;
  /** Controls that belong to this section, aligned opposite the heading. */
  actions?: React.ReactNode;
  /** Tighter padding for dense panels and side rails. */
  compact?: boolean;
}

/**
 * One band inside a card, separated from its neighbours by a hairline.
 *
 * This exists to kill card-in-card nesting. A panel that needs three labelled
 * groups wants three `CardSection`s inside one `Card`, not three nested
 * `Card`s: the nested version draws nine borders and two shadows to express
 * one level of hierarchy, and at projector distance the frames read louder
 * than the data inside them.
 */
const CardSection = React.forwardRef<HTMLDivElement, CardSectionProps>(
  (
    { className, title, description, actions, compact, children, ...props },
    ref,
  ) => (
    <div
      ref={ref}
      className={cn(
        'border-t first:border-t-0',
        compact ? 'px-4 py-4' : 'px-6 py-5',
        className,
      )}
      {...props}
    >
      {title || actions || description ? (
        <div className="mb-3 flex items-start justify-between gap-3">
          <div className="min-w-0 space-y-1">
            {title ? (
              <h4 className="text-sm font-semibold leading-none">{title}</h4>
            ) : null}
            {description ? (
              <p className="text-sm text-muted-foreground">{description}</p>
            ) : null}
          </div>
          {actions ? (
            <div className="flex shrink-0 items-center gap-2">{actions}</div>
          ) : null}
        </div>
      ) : null}
      {children}
    </div>
  ),
);
CardSection.displayName = 'CardSection';

export {
  Card,
  CardHeader,
  CardFooter,
  CardTitle,
  CardDescription,
  CardContent,
  CardSection,
};
