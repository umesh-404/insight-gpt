'use client';

/**
 * Last-resort boundary: catches errors thrown by the root layout itself, where
 * no app chrome (and no global stylesheet) is guaranteed to exist. It must
 * render its own <html>/<body>, and every value here has to be inline —
 * including the dark-mode palette, which is why there is a <style> block
 * rather than the token set this page cannot reach.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <head>
        <title>InsightGPT failed to load</title>
        <style
          // Tokens mirroring globals.css closely enough that this page does not
          // look like a different product, and a `prefers-color-scheme` branch
          // so it is not a white flash for a dark-theme user.
          dangerouslySetInnerHTML={{
            __html: `
:root {
  color-scheme: light dark;
  --bg: #f7f9fc;
  --card: #ffffff;
  --fg: #0f172a;
  --muted: #434d61;
  --line: #c6cedb;
  --danger: #c41f1f;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1322;
    --card: #131a2a;
    --fg: #e6ecf5;
    --muted: #a8b3c6;
    --line: #3f4858;
    --danger: #f98b8b;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1.5rem;
  background: var(--bg);
  color: var(--fg);
  font: 15px/1.6 'Inter', ui-sans-serif, system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
}
.panel {
  width: 100%;
  max-width: 28rem;
  padding: 1.5rem;
  border: 1px solid var(--line);
  border-radius: 0.5rem;
  background: var(--card);
}
h1 { margin: 0; font-size: 1.0625rem; font-weight: 600; letter-spacing: -0.01em; }
p { margin: 0.5rem 0 0; color: var(--muted); font-size: 0.875rem; }
.ref {
  margin-top: 0.875rem;
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 0.75rem;
  word-break: break-all;
}
button {
  margin-top: 1.25rem;
  min-height: 2.5rem;
  padding: 0 1rem;
  border: 1px solid var(--line);
  border-radius: 0.375rem;
  background: transparent;
  color: inherit;
  font: inherit;
  font-weight: 500;
  cursor: pointer;
}
button:hover { border-color: var(--muted); }
button:focus-visible { outline: 2px solid var(--danger); outline-offset: 2px; }
.rule { height: 2px; width: 2rem; margin-bottom: 1rem; background: var(--danger); }
`,
          }}
        />
      </head>
      <body>
        <div className="panel">
          <div className="rule" aria-hidden />
          <h1>InsightGPT failed to load</h1>
          <p>
            {error.message ||
              'An unexpected error stopped the application shell from starting.'}
          </p>
          <p>
            Reloading usually clears it. If it does not, the API may be
            unreachable — check that the backend is running.
          </p>
          {error.digest ? <p className="ref">Reference {error.digest}</p> : null}
          <button type="button" onClick={reset}>
            Reload the application
          </button>
        </div>
      </body>
    </html>
  );
}
