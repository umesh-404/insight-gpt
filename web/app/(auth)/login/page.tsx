'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { Loader2, ShieldCheck } from 'lucide-react';
import { Logo } from '@/components/layout/logo';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useAuth } from '@/lib/auth';
import { USE_MOCK } from '@/lib/api';
import { ApiError } from '@/lib/types';

export default function LoginPage() {
  const router = useRouter();
  const { login, user, loading } = useAuth();
  // Prefill only in demo mode; real mode must never suggest credentials.
  const [email, setEmail] = React.useState(USE_MOCK ? 'demo@insightgpt.local' : '');
  const [password, setPassword] = React.useState(USE_MOCK ? 'demo' : '');
  const [submitting, setSubmitting] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!loading && user) router.replace('/ask');
  }, [loading, user, router]);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      router.replace('/ask');
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : 'Unable to sign in. Check your credentials and try again.',
      );
    } finally {
      setSubmitting(false);
    }
  };

  // While the silent refresh probe is still running we do not yet know whether
  // there is a session — showing the form would flash it for a signed-in user.
  if (loading) {
    return (
      <div
        className="flex min-h-screen items-center justify-center bg-background"
        aria-busy="true"
      >
        <Loader2 className="size-6 animate-spin text-muted-foreground" aria-hidden />
        <span className="sr-only">Checking your session…</span>
      </div>
    );
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      {/* Brand panel. Deep, near-black blue rather than flat primary: the
          wordmark and the accent hue stay readable on top of it, and it reads
          as an enterprise console instead of a marketing splash. */}
      <div className="relative hidden flex-col justify-between overflow-hidden bg-[hsl(224_45%_10%)] p-12 text-white lg:flex">
        <div
          className="absolute inset-0 opacity-[0.12]"
          style={{
            backgroundImage:
              'linear-gradient(to right, white 1px, transparent 1px), linear-gradient(to bottom, white 1px, transparent 1px)',
            backgroundSize: '48px 48px',
          }}
          aria-hidden
        />
        <div
          className="absolute -left-24 -top-24 size-[28rem] rounded-full bg-[hsl(224_76%_48%)] opacity-30 blur-3xl"
          aria-hidden
        />
        <div
          className="absolute -bottom-32 -right-20 size-[24rem] rounded-full bg-[hsl(199_82%_40%)] opacity-25 blur-3xl"
          aria-hidden
        />

        <div className="relative">
          <Logo className="[&_span]:text-white [&_span_span]:text-[hsl(214_95%_72%)]" />
        </div>

        <div className="relative max-w-md">
          <h2 className="text-4xl font-semibold leading-[1.15]">
            Explainable answers over all your business data.
          </h2>
          <p className="mt-4 leading-relaxed text-white/70">
            Ask in plain English. Every answer shows the SQL that produced the
            numbers and the documents behind each claim — never a black box.
          </p>
          <ul className="mt-8 space-y-3.5 text-sm text-white/85">
            {[
              'Conversational analytics with streamed, cited answers',
              'Governed dashboards over a semantic metric layer',
              'Anomaly detection with deterministic root causes',
              'Executive reports, exportable to PDF',
            ].map((line) => (
              <li key={line} className="flex items-start gap-2.5">
                <ShieldCheck
                  className="mt-0.5 size-4 shrink-0 text-[hsl(214_95%_72%)]"
                  aria-hidden
                />
                {line}
              </li>
            ))}
          </ul>
        </div>

        <div className="relative text-xs text-white/50">
          Read-only analytics · Role-based access · Private by default
        </div>
      </div>

      {/* Form panel */}
      <div className="flex items-center justify-center p-6">
        <div className="w-full max-w-sm">
          <div className="mb-8 lg:hidden">
            <Logo />
          </div>
          <h1 className="text-3xl font-semibold">Welcome back</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Sign in to your InsightGPT workspace.
          </p>

          <form onSubmit={onSubmit} className="mt-8 space-y-4" noValidate>
            <div className="space-y-1.5">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
              />
            </div>

            {error ? (
              <p
                role="alert"
                className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
              >
                {error}
              </p>
            ) : null}

            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting ? (
                <>
                  <Loader2 className="size-4 animate-spin" /> Signing in…
                </>
              ) : (
                'Sign in'
              )}
            </Button>
          </form>

          {USE_MOCK ? (
            <p className="mt-6 rounded-md border border-dashed bg-muted/40 px-3 py-2 text-center text-xs text-muted-foreground">
              Demo mode is on — any credentials sign you in as an admin.
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
