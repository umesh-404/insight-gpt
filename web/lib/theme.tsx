'use client';

import * as React from 'react';
import { THEME_ATTRIBUTE, THEME_STORAGE_KEY } from './theme-script';

type Theme = 'light' | 'dark';

interface ThemeContextValue {
  theme: Theme;
  toggleTheme: () => void;
  setTheme: (theme: Theme) => void;
}

const ThemeContext = React.createContext<ThemeContextValue | null>(null);

const STORAGE_KEY = THEME_STORAGE_KEY;

/** Applied both here and by the inline script in the root layout (no FOUC). */
function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  root.classList.toggle('dark', theme === 'dark');
  root.setAttribute(THEME_ATTRIBUTE, theme);
  root.style.colorScheme = theme;
}

/** Whatever the pre-hydration script already painted. */
function resolvedTheme(): Theme {
  return document.documentElement.getAttribute(THEME_ATTRIBUTE) === 'dark'
    ? 'dark'
    : 'light';
}

/**
 * `useLayoutEffect` on the client, `useEffect` on the server.
 *
 * This is the whole fix for the one-frame theme desync. The provider has to
 * start from a server-renderable guess ('light') because the DOM does not
 * exist during SSR, so for one commit the *state* says light while the *page*
 * is already dark. A plain `useEffect` corrects that after the browser has
 * painted, which is exactly one visible wrong frame — long enough to see a
 * white sun icon flash on a dark header. A layout effect runs after the DOM is
 * mutated but before paint, so the correction is never shown.
 *
 * React warns about `useLayoutEffect` during SSR, hence the swap: this file is
 * a client component but is still rendered on the server.
 */
const useIsomorphicLayoutEffect =
  typeof window === 'undefined' ? React.useEffect : React.useLayoutEffect;

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = React.useState<Theme>('light');

  // Adopt the script's decision before the first paint. No re-derivation, so
  // the provider cannot disagree with what is already on screen.
  useIsomorphicLayoutEffect(() => {
    setThemeState(resolvedTheme());
  }, []);

  const setTheme = React.useCallback((next: Theme) => {
    setThemeState(next);
    applyTheme(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Blocked storage (private mode): the choice still applies to this tab.
    }
  }, []);

  // Follow the OS only while the user has made no explicit choice — once they
  // have picked a theme, a system change must not override them.
  React.useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const onSystemChange = (event: MediaQueryListEvent) => {
      let stored: string | null = null;
      try {
        stored = window.localStorage.getItem(STORAGE_KEY);
      } catch {
        stored = null;
      }
      if (stored === 'light' || stored === 'dark') return;
      const next: Theme = event.matches ? 'dark' : 'light';
      setThemeState(next);
      applyTheme(next);
    };
    media.addEventListener('change', onSystemChange);
    return () => media.removeEventListener('change', onSystemChange);
  }, []);

  // Two tabs of the same workspace is the normal demo setup; without this the
  // second tab keeps the stale theme until it is reloaded.
  React.useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== STORAGE_KEY) return;
      if (event.newValue !== 'light' && event.newValue !== 'dark') return;
      setThemeState(event.newValue);
      applyTheme(event.newValue);
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  const toggleTheme = React.useCallback(() => {
    setThemeState((current) => {
      const next: Theme = current === 'dark' ? 'light' : 'dark';
      applyTheme(next);
      try {
        window.localStorage.setItem(STORAGE_KEY, next);
      } catch {
        // See setTheme.
      }
      return next;
    });
  }, []);

  const value = React.useMemo<ThemeContextValue>(
    () => ({ theme, toggleTheme, setTheme }),
    [theme, toggleTheme, setTheme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = React.useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used within <ThemeProvider>');
  return ctx;
}
