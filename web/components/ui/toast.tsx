'use client';

import * as React from 'react';
import { CheckCircle2, Info, X, TriangleAlert } from 'lucide-react';
import { cn } from '@/lib/utils';

type ToastVariant = 'default' | 'success' | 'warning' | 'destructive';

interface ToastItem {
  id: string;
  title: string;
  description?: React.ReactNode;
  variant: ToastVariant;
}

interface ToastContextValue {
  toast: (t: {
    title: string;
    description?: React.ReactNode;
    variant?: ToastVariant;
  }) => void;
}

const ToastContext = React.createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const ctx = React.useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within <ToastProvider>');
  return ctx;
}

const ICONS: Record<ToastVariant, React.ReactNode> = {
  default: <Info className="size-4 text-primary" />,
  success: <CheckCircle2 className="size-4 text-success" />,
  warning: <TriangleAlert className="size-4 text-warning" />,
  destructive: <TriangleAlert className="size-4 text-destructive" />,
};

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = React.useState<ToastItem[]>([]);

  const dismiss = React.useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toast = React.useCallback<ToastContextValue['toast']>(
    ({ title, description, variant = 'default' }) => {
      const id = Math.random().toString(36).slice(2);
      setToasts((prev) => [...prev, { id, title, description, variant }]);
      window.setTimeout(() => dismiss(id), 6000);
    },
    [dismiss],
  );

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div
        aria-live="polite"
        aria-atomic="true"
        className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-full max-w-sm flex-col gap-2"
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            // Failures interrupt; confirmations wait their turn.
            role={t.variant === 'destructive' ? 'alert' : 'status'}
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
            <div className="flex-1">
              <p className="text-sm font-medium text-card-foreground">
                {t.title}
              </p>
              {t.description ? (
                <div className="mt-1 text-sm text-muted-foreground">
                  {t.description}
                </div>
              ) : null}
            </div>
            <button
              type="button"
              onClick={() => dismiss(t.id)}
              className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              aria-label="Dismiss notification"
            >
              <X className="size-4" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
