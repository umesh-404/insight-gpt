import { cn } from '@/lib/utils';

/**
 * Wordmark used in the sidebar, the login screen and the mobile header.
 *
 * The mark is drawn here rather than borrowed from the icon set. The previous
 * sparkle is the default glyph for "something clever happens inside", which is
 * true of roughly every product and says nothing about this one; three bars
 * against an axis says *analytics* at 32px, which is the only size that
 * matters. It is also the one place a chart appears without data behind it, so
 * it is deliberately abstract — a plotted shape, not a fake metric.
 */
export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn('flex items-center gap-2.5', className)}>
      <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
        <svg
          viewBox="0 0 20 20"
          className="size-[18px]"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          aria-hidden
        >
          {/* Axis, then three readings of increasing height. */}
          <path d="M3.5 3.5v13h13" opacity="0.55" />
          <path d="M7 13V9.5" />
          <path d="M10.5 13V6.5" />
          <path d="M14 13v-2" />
        </svg>
      </span>
      <span className="text-lg font-semibold tracking-tight">
        Insight<span className="text-primary">GPT</span>
      </span>
    </span>
  );
}
