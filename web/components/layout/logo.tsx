import { Sparkles } from 'lucide-react';
import { cn } from '@/lib/utils';

/** Wordmark used in the sidebar and login screen. */
export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn('flex items-center gap-2', className)}>
      <span className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-sm">
        <Sparkles className="size-4" aria-hidden />
      </span>
      <span className="text-base font-semibold tracking-tight">
        Insight<span className="text-primary">GPT</span>
      </span>
    </span>
  );
}
