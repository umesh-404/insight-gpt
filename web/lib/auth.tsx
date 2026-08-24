'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { api, accessTokenTtl, onAccessToken, refreshAccessToken } from './api';
import type { Role, User } from './types';
import { roleAtLeast } from './types';

interface AuthContextValue {
  user: User | null;
  /** True until the initial silent-refresh probe settles. */
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  hasRole: (minimum: Role) => boolean;
}

const AuthContext = React.createContext<AuthContextValue | null>(null);

/** Renew this many seconds before the access token actually expires. */
const REFRESH_SKEW_SECONDS = 60;

/**
 * Session provider.
 *
 * The refresh token lives in the httpOnly `igpt_refresh` cookie, which JS
 * cannot read — so session restore works by *attempting* a refresh rather than
 * trusting a localStorage marker. On mount:
 *
 *   POST /auth/refresh (no body, cookie attached)
 *     ↳ 200 → GET /auth/me → session restored
 *     ↳ 401 → no session; the (app) guard redirects to /login
 *
 * While signed in, a timer renews the token shortly before `expires_in`
 * elapses, so a long-lived tab never has to recover from a 401.
 */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = React.useState<User | null>(null);
  const [loading, setLoading] = React.useState(true);
  const timerRef = React.useRef<number | null>(null);

  const clearTimer = React.useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  // Schedule a proactive refresh whenever a new access token is issued.
  React.useEffect(() => {
    const schedule = (token: string | null, expiresIn: number) => {
      clearTimer();
      if (!token) {
        // The token was dropped (failed refresh or sign-out): end the session
        // so the route guard can redirect instead of leaving a dead shell.
        setUser(null);
        return;
      }
      if (!expiresIn) return;
      const delayMs = Math.max(5, expiresIn - REFRESH_SKEW_SECONDS) * 1000;
      timerRef.current = window.setTimeout(() => {
        void refreshAccessToken().then((ok) => {
          if (!ok) setUser(null);
        });
      }, delayMs);
    };
    const unsubscribe = onAccessToken(schedule);
    // Cover a token that was already issued before this effect ran.
    const ttl = accessTokenTtl();
    if (Number.isFinite(ttl) && ttl > 0) schedule('existing', ttl);
    return () => {
      unsubscribe();
      clearTimer();
    };
  }, [clearTimer]);

  // Initial silent restore.
  React.useEffect(() => {
    let active = true;
    api
      .restoreSession()
      .then((restored) => {
        if (active) setUser(restored);
      })
      .catch(() => {
        if (active) setUser(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const login = React.useCallback(async (email: string, password: string) => {
    const { user: signedIn } = await api.login({ email, password });
    setUser(signedIn);
  }, []);

  const logout = React.useCallback(async () => {
    clearTimer();
    try {
      await api.logout();
    } finally {
      setUser(null);
    }
  }, [clearTimer]);

  const hasRole = React.useCallback(
    (minimum: Role) => (user ? roleAtLeast(user.role, minimum) : false),
    [user],
  );

  const value = React.useMemo<AuthContextValue>(
    () => ({ user, loading, login, logout, hasRole }),
    [user, loading, login, logout, hasRole],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = React.useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within <AuthProvider>');
  return ctx;
}

/** Guard hook for the (app) route group — redirects to /login when signed out. */
export function useRequireAuth(): AuthContextValue {
  const auth = useAuth();
  const router = useRouter();
  React.useEffect(() => {
    if (!auth.loading && !auth.user) router.replace('/login');
  }, [auth.loading, auth.user, router]);
  return auth;
}
