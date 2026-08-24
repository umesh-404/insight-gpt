'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { api } from './api';
import type { Role, User } from './types';
import { roleAtLeast } from './types';

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  hasRole: (minimum: Role) => boolean;
}

const AuthContext = React.createContext<AuthContextValue | null>(null);

/**
 * Client-side session marker. In production, session validity is proven by the
 * httpOnly refresh cookie and re-checked server-side in the (app) layout
 * (docs/07-frontend.md §6.2). This marker lets the demo restore a session and
 * makes the login route reachable without a backend when mock mode is on.
 */
const SESSION_KEY = 'igpt-session';

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = React.useState<User | null>(null);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    let active = true;
    const marker = window.localStorage.getItem(SESSION_KEY);
    if (!marker) {
      setLoading(false);
      return;
    }
    api
      .me()
      .then((u) => {
        if (active) setUser(u);
      })
      .catch(() => {
        window.localStorage.removeItem(SESSION_KEY);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const login = React.useCallback(async (email: string, password: string) => {
    const { user: u } = await api.login({ email, password });
    window.localStorage.setItem(SESSION_KEY, '1');
    setUser(u);
  }, []);

  const logout = React.useCallback(async () => {
    await api.logout();
    window.localStorage.removeItem(SESSION_KEY);
    setUser(null);
  }, []);

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
