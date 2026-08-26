import { cn } from '@/lib/utils';

/**
 * Loading placeholder with a travelling sheen.
 *
 * The sheen (rather than a flat opacity pulse) is what makes a skeleton read as
 * "data is on its way" instead of "this element is disabled". It is a pure
 * decoration: the reduced-motion rule in globals.css stops it, leaving a plain
 * muted block.
 */
function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('relative overflow-hidden rounded-md bg-muted', className)}
      aria-hidden
      {...props}
    >
      <span className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-foreground/[0.06] to-transparent" />
    </div>
  );
}

export { Skeleton };
