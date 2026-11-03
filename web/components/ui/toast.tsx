'use client';

import * as React from 'react';
import { CheckCircle2, Info, X, TriangleAlert, XCircle } from 'lucide-react';
import { cn } from '@/lib/utils';

type ToastVariant = 'default' | 'success' | 'warning' | 'destructive';

interface ToastItem {
  id: string;
  title: string;
  description?: React.ReactNode;
  variant: ToastVariant;
  action?: React.ReactNode;
  duration: number;
}

interface ToastContextValue {
  toast: (t: {
    title: string;
    description?: React.ReactNode;
    variant?: ToastVariant;
    /**
     * One control, for a recovery path the user would otherwise have to go
     * find ("Open report", "Undo"). Not a second copy of the primary action.
     */
    action?: React.ReactNode;
    /**
     * Milliseconds before auto-dismiss. `0` (or any non-finite value) pins the
     * toast until it is dismissed by hand — which is the default for
     * `destructive`, because a failure the user blinked past is a failure they
     * will report as "it just did nothing".
     */
    duration?: number;
  }) => void;
}

const ToastContext = React.createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const ctx = React.useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within <ToastProvider>');
  return ctx;
}

/**
 * Four distinct icon *shapes*, not four colours of the same triangle.
 *
 * Warning and failure previously shared `TriangleAlert`, which made the
 * difference between "that worked but look at this" and "that did not work"
 * carried by hue alone — unreadable to a red-green colour-blind viewer and to
 * anyone watching a washed-out projector. Circle/tick, triangle, circle/cross
 * and circle/i are all tellable apart in greyscale.
 */
const ICONS: Record<ToastVariant, React.ReactNode> = {
  default: <Info className="size-4 text-primary" aria-hidden />,
  success: <CheckCircle2 className="size-4 text-success" aria-hidden />,
  warning: <TriangleAlert className="size-4 text-warning" aria-hidden />,
  destructive: <XCircle className="size-4 text-destructive" aria-hidden />,
};

/** Spoken prefix, so the variant is not conveyed visually only. */
const SPOKEN: Record<ToastVariant, string> = {
  default: 'Note:',
  success: 'Success:',
  warning: 'Warning:',
  destructive: 'Error:',
};

/** A burst of failures must not bury the page under a column of cards. */
const MAX_VISIBLE = 4;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = React.useState<ToastItem[]>([]);
  const timers = React.useRef(new Map<string, number>());

  const clearTimer = React.useCallback((id: string) => {
    const handle = timers.current.get(id);
    if (handle !== undefined) {
      window.clearTimeout(handle);
      timers.current.delete(id);
    }
  }, []);

  const dismiss = React.useCallback(
    (id: string) => {
      clearTimer(id);
      setToasts((prev) => prev.filter((t) => t.id !== id));
    },
    [clearTimer],
  );

  const startTimer = React.useCallback(
    (id: string, duration: number) => {
      if (!Number.isFinite(duration) || duration <= 0) return;
      clearTimer(id);
      timers.current.set(id, window.setTimeout(() => dismiss(id), duration));
    },
    [clearTimer, dismiss],
  );

  const toast = React.useCallback<ToastContextValue['toast']>(
    ({ title, description, variant = 'default', action, duration }) => {
      const id = Math.random().toString(36).slice(2);
      const resolved =
        duration ?? (variant === 'destructive' ? 0 : variant === 'warning' ? 9000 : 6000);
      setToasts((prev) =>
        [...prev, { id, title, description, variant, action, duration: resolved }].slice(
          -MAX_VISIBLE,
        ),
      );
      startTimer(id, resolved);
    },
    [startTimer],
  );

  // Timers are wall-clock, so anything still pending when the provider goes
  // away would fire against an unmounted tree.
  React.useEffect(() => {
    const handles = timers.current;
    return () => {
      handles.forEach((handle) => window.clearTimeout(handle));
      handles.clear();
    };
  }, []);

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div
        aria-live="polite"
        aria-atomic="true"
        // `w-full` with a right inset overflows a 375px viewport by the inset;
        // subtract the gutters below the `sm` breakpoint instead.
        className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-[calc(100%-2rem)] max-w-sm flex-col gap-2 sm:w-full"
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            // Failures interrupt; confirmations wait their turn.
            role={t.variant === 'destructive' ? 'alert' : 'status'}
            // Reading a toast should not race its own timer.
            onMouseEnter={() => clearTimer(t.id)}
            onFocus={() => clearTimer(t.id)}
            onMouseLeave={() => startTimer(t.id, t.duration)}
            className={cn(
              // Floats above everything, so it takes the overlay elevation and
              // a variant-coloured rail that reads before the icon does.
              'pointer-events-auto relative flex animate-fade-in items-start gap-3 overflow-hidden rounded-lg border bg-card p-4 pl-5 shadow-overlay',
              'before:absolute before:inset-y-0 before:left-0 before:w-1 before:content-[""]',
              t.variant === 'success' && 'before:bg-success',
              t.variant === 'warning' && 'before:bg-warning',
              t.variant === 'destructive' && 'before:bg-destructive',
              t.variant === 'default' && 'before:bg-primary',
            )}
          >
            <div className="mt-0.5">{ICONS[t.variant]}</div>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-card-foreground">
                <span className="sr-only">{SPOKEN[t.variant]} </span>
                {t.title}
              </p>
              {t.description ? (
                <div className="mt-1 break-words text-sm text-muted-foreground">
                  {t.description}
                </div>
              ) : null}
              {t.action ? <div className="mt-3">{t.action}</div> : null}
            </div>
            <button
              type="button"
              onClick={() => dismiss(t.id)}
              // Negative margin keeps the optical alignment while the hit area
              // grows past the 16px glyph — 32px with a mouse, 40px with a thumb.
              className="-m-2 rounded-md p-2 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [@media(pointer:coarse)]:p-3"
              aria-label="Dismiss notification"
            >
              <X className="size-4" aria-hidden />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
