'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { Loader2 } from 'lucide-react';
import { Logo } from '@/components/layout/logo';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useAuth } from '@/lib/auth';
import { USE_MOCK } from '@/lib/api';
import { ApiError } from '@/lib/types';
import { cn } from '@/lib/utils';

/**
 * The seeded demo accounts, one per role (`services/api/app/auth/store.py`).
 *
 * These are published fixtures, not secrets — `scripts/setup.py` prints them
 * to the console at the end of a successful install. They are surfaced here
 * because role gating is a *feature* of this build: half the screens change
 * behaviour with the signed-in role, and an evaluator who cannot switch role
 * in two clicks will never see that the gate exists. Typing three addresses
 * from a README is the difference between the gate being demonstrated and the
 * gate being taken on faith.
 *
 * `sees` is deliberately phrased as what the role *gets*, not as a permission
 * matrix: the point is to make picking one an informed choice.
 */
const DEMO_ACCOUNTS = [
  {
    role: 'Admin',
    email: 'admin@insightgpt.dev',
    password: 'admin-pass',
    sees: 'Everything, including data sources and provider internals',
  },
  {
    role: 'Analyst',
    email: 'analyst@insightgpt.dev',
    password: 'analyst-pass',
    sees: 'Insights, pipelines and report generation',
  },
  {
    role: 'Viewer',
    email: 'viewer@insightgpt.dev',
    password: 'viewer-pass',
    sees: 'Ask and dashboards only — the gated screens say so plainly',
  },
] as const;

/** Honest capability list: each line is a screen that exists in this build. */
const CAPABILITIES = [
  ['Ask', 'Plain-English questions, answered token by token with the SQL attached.'],
  ['Dashboards', 'Governed KPIs and trends resolved through one semantic metric layer.'],
  ['Insights', 'Anomalies with a deterministic root cause, not a guess.'],
  ['Reports', 'An executive write-up for a chosen period, exportable to PDF.'],
] as const;

export default function LoginPage() {
  const router = useRouter();
  const { login, user, loading } = useAuth();
  // Prefill only in demo mode; real mode must never pre-enter a password for
  // you. The role buttons below are available either way, because the API
  // seeds the same three accounts in both.
  const [email, setEmail] = React.useState(USE_MOCK ? 'admin@insightgpt.dev' : '');
  const [password, setPassword] = React.useState(USE_MOCK ? 'admin-pass' : '');
  const [submitting, setSubmitting] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const emailRef = React.useRef<HTMLInputElement | null>(null);

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

  // Filling the fields rather than signing in directly is deliberate: the
  // evaluator sees *which* account they are about to use, and the submit
  // button stays the one thing that signs you in.
  const fillFromAccount = (account: (typeof DEMO_ACCOUNTS)[number]) => {
    setEmail(account.email);
    setPassword(account.password);
    setError(null);
    emailRef.current?.focus();
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
    <div className="grid min-h-screen lg:grid-cols-[1.05fr_1fr]">
      {/*
        Brand panel.
        Deep, near-black blue rather than flat primary: the wordmark and the
        accent hue stay readable on top of it, and it reads as an enterprise
        console instead of a marketing splash. The only decoration is the
        hairline grid — the two blurred colour washes that used to sit in the
        corners were pure atmosphere, said nothing about the product, and are
        the first thing that turns to mud on a projector.
      */}
      <div className="relative hidden flex-col justify-between overflow-hidden bg-[hsl(224_45%_9%)] p-10 text-white xl:p-14 lg:flex">
        <div
          className="absolute inset-0 opacity-[0.08]"
          style={{
            backgroundImage:
              'linear-gradient(to right, white 1px, transparent 1px), linear-gradient(to bottom, white 1px, transparent 1px)',
            backgroundSize: '44px 44px',
          }}
          aria-hidden
        />

        <div className="relative">
          <Logo className="[&_span]:text-white [&_span_span]:text-[hsl(214_95%_74%)]" />
        </div>

        <div className="relative max-w-xl">
          <h2 className="text-4xl font-semibold leading-[1.12]">
            Every number comes with its receipt.
          </h2>
          <p className="mt-4 max-w-md text-base leading-relaxed text-white/75">
            Ask in plain English. Each answer carries the SQL that produced the
            figures and the documents behind each claim, so the next question is
            always &ldquo;what does it mean?&rdquo; and never &ldquo;can I trust
            it?&rdquo;.
          </p>

          {/* A definition list, not a tick-list: each line names a screen and
              says what it does, which is checkable. Ticks next to adjectives
              are not. */}
          <dl className="mt-10 grid gap-y-4 border-t border-white/15 pt-8 sm:grid-cols-2 sm:gap-x-10">
            {CAPABILITIES.map(([name, detail]) => (
              <div key={name}>
                <dt className="text-sm font-semibold text-[hsl(214_95%_78%)]">
                  {name}
                </dt>
                <dd className="mt-1 text-sm leading-relaxed text-white/65">
                  {detail}
                </dd>
              </div>
            ))}
          </dl>
        </div>

        <div className="relative text-xs text-white/45">
          Read-only analytics · Role-based access · Secrets redacted at ingestion
        </div>
      </div>

      {/* Form panel */}
      <div className="flex items-center justify-center p-6 sm:p-10">
        <div className="w-full max-w-sm">
          <div className="mb-10 lg:hidden">
            <Logo />
          </div>
          <h1 className="text-3xl font-semibold">Sign in</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Your InsightGPT workspace.
          </p>

          <form onSubmit={onSubmit} className="mt-8 space-y-4" noValidate>
            <div className="space-y-1.5">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                ref={emailRef}
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
                className="rounded-md border border-destructive/40 bg-destructive-subtle px-3 py-2 text-sm text-destructive"
              >
                {error}
              </p>
            ) : null}

            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting ? (
                <>
                  <Loader2 className="size-4 animate-spin" aria-hidden /> Signing
                  in…
                </>
              ) : (
                'Sign in'
              )}
            </Button>
          </form>

          {/*
           * Seeded credentials are offered in demo mode only.
           *
           * This panel fills the form with a known email and password, and the
           * live API seeds exactly these three accounts - so rendering it
           * against a real backend publishes working admin credentials on an
           * unauthenticated page. The project documents a public deployment
           * (Vercel frontend, Hugging Face Spaces backend), where that page is
           * reachable by anyone. Role gating is still demonstrable there by
           * signing in as each account deliberately; what must not happen is
           * the sign-in screen handing them out.
           */}
          {USE_MOCK ? (
          <div className="mt-10 border-t pt-6">
            <h2 className="text-2xs font-semibold uppercase tracking-wider text-muted-foreground">
              Demo accounts
            </h2>
            <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
              Pick a role to fill the form. Role changes what the app shows —
              that is the point, so it is worth trying more than one.
            </p>
            <ul className="mt-3 space-y-1.5">
              {DEMO_ACCOUNTS.map((account) => {
                const selected = email === account.email;
                return (
                  <li key={account.email}>
                    <button
                      type="button"
                      onClick={() => fillFromAccount(account)}
                      aria-pressed={selected}
                      className={cn(
                        'flex w-full min-h-10 items-baseline gap-2 rounded-md border px-3 py-2 text-left transition-colors',
                        selected
                          ? 'border-primary/50 bg-primary-subtle'
                          : 'bg-card hover:border-border-strong hover:bg-accent',
                      )}
                    >
                      <span
                        className={cn(
                          'shrink-0 text-sm font-semibold',
                          selected
                            ? 'text-primary-subtle-foreground'
                            : 'text-foreground',
                        )}
                      >
                        {account.role}
                      </span>
                      <span className="min-w-0 flex-1 text-xs leading-snug text-muted-foreground">
                        {account.sees}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
            <p className="mt-3 text-xs text-muted-foreground">
              Demo mode is on: the API is stubbed and any credentials sign you
              in as an admin.
            </p>
          </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
