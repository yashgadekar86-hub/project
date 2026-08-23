"use client";

import { create } from "zustand";
import { useEffect } from "react";
import { api, ApiError, AUTH_LOGOUT_EVENT } from "@/services/api";

export type User = {
  id: string;
  email: string;
  username: string;
  role: string;
  full_name?: string | null;
};

type AuthState = {
  user: User | null;
  token: string | null;
  loading: boolean;
  hydrate: () => Promise<void>;
  login: (email_or_username: string, password: string) => Promise<void>;
  register: (email: string, username: string, password: string, full_name?: string) => Promise<void>;
  logout: () => void;
  _setUser: (user: User, token: string) => void;
  _clearUser: () => void;
};

const TOKEN_KEY = "aifx_token";
const REFRESH_KEY = "aifx_refresh";

// Dev-bypass mode: no login screen, no async auth handshake. We seed the
// store with a hardcoded demo user and a magic bearer token that the
// backend accepts (only in APP_ENV=development). The first real /me call
// will hydrate the full server-side user record, but for rendering the
// shell/dashboard we don't need to wait for anything.
export const DEV_BYPASS = process.env.NEXT_PUBLIC_DEV_AUTH_BYPASS === "true";
const DEV_MAGIC_TOKEN = "dev-bypass-aifx-demo-user";
const DEV_SEED_USER: User = {
  id: "00000000-0000-0000-0000-000000000000",
  email: "demo@example.com",
  username: "demo",
  full_name: "Demo Trader",
  role: "TRADER",
};

function readToken() {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

// In dev-bypass we treat the magic token as always present.
function effectiveToken(): string | null {
  if (typeof window === "undefined") return null;
  if (DEV_BYPASS) return DEV_MAGIC_TOKEN;
  return window.localStorage.getItem(TOKEN_KEY);
}

function installDevToken() {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(TOKEN_KEY, DEV_MAGIC_TOKEN);
  } catch {}
}

export const useAuth = create<AuthState>((set) => ({
  // Seed state synchronously so the first render already has a user and
  // ClientLayout never shows the loader or triggers a login redirect.
  user: DEV_BYPASS ? DEV_SEED_USER : null,
  token: DEV_BYPASS ? DEV_MAGIC_TOKEN : null,
  loading: !DEV_BYPASS,

  _setUser: (user, token) => {
    if (typeof window !== "undefined") window.localStorage.setItem(TOKEN_KEY, token);
    set({ user, token, loading: false });
  },

  _clearUser: () => {
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(TOKEN_KEY);
      window.localStorage.removeItem(REFRESH_KEY);
      if (DEV_BYPASS) {
        // In dev-bypass, never stay logged out — immediately re-seed.
        installDevToken();
        set({ user: DEV_SEED_USER, token: DEV_MAGIC_TOKEN, loading: false });
        return;
      }
    }
    set({ user: null, token: null, loading: false });
  },

  hydrate: async () => {
    if (DEV_BYPASS) {
      installDevToken();
      // Fire-and-forget a /me to replace the seed user with the real
      // server-side record (id, role, full_name, etc.) once available.
      // We don't await it — the shell renders immediately with the seed.
      api.get<User>("/api/v1/auth/me")
        .then((me) => set({ user: me, token: DEV_MAGIC_TOKEN, loading: false }))
        .catch(() => {
          // Even if /me fails, we keep the seed user so the UI doesn't
          // collapse to a login screen.
          set({ user: DEV_SEED_USER, token: DEV_MAGIC_TOKEN, loading: false });
        });
      set({ user: DEV_SEED_USER, token: DEV_MAGIC_TOKEN, loading: false });
      return;
    }

    const t = readToken();
    if (t) {
      try {
        const me = await api.get<User>("/api/v1/auth/me");
        set({ user: me, token: t, loading: false });
        return;
      } catch (e) {
        const r = typeof window !== "undefined" ? window.localStorage.getItem(REFRESH_KEY) : null;
        if (r && e instanceof ApiError && e.status === 401) {
          try {
            const refreshed = await api.post<{ access_token: string; refresh_token: string; user: User }>(
              "/api/v1/auth/refresh", { refresh_token: r }
            );
            if (typeof window !== "undefined") {
              window.localStorage.setItem(TOKEN_KEY, refreshed.access_token);
              if (refreshed.refresh_token) window.localStorage.setItem(REFRESH_KEY, refreshed.refresh_token);
            }
            set({ user: refreshed.user, token: refreshed.access_token, loading: false });
            return;
          } catch {
            // fall through
          }
        }
        if (typeof window !== "undefined") {
          window.localStorage.removeItem(TOKEN_KEY);
          window.localStorage.removeItem(REFRESH_KEY);
        }
      }
    }
    set({ user: null, token: null, loading: false });
  },

  login: async (e, p) => {
    if (DEV_BYPASS) {
      installDevToken();
      set({ user: DEV_SEED_USER, token: DEV_MAGIC_TOKEN, loading: false });
      return;
    }
    const r = await api.post<{ access_token: string; refresh_token: string; user: User }>(
      "/api/v1/auth/login", { email_or_username: e, password: p }
    );
    if (typeof window !== "undefined") {
      window.localStorage.setItem(TOKEN_KEY, r.access_token);
      if (r.refresh_token) window.localStorage.setItem(REFRESH_KEY, r.refresh_token);
    }
    set({ user: r.user, token: r.access_token, loading: false });
  },

  register: async (email, username, password, full_name) => {
    if (DEV_BYPASS) {
      installDevToken();
      set({ user: DEV_SEED_USER, token: DEV_MAGIC_TOKEN, loading: false });
      return;
    }
    const r = await api.post<{ access_token: string; refresh_token: string; user: User }>(
      "/api/v1/auth/register", { email, username, password, full_name }
    );
    if (typeof window !== "undefined") {
      window.localStorage.setItem(TOKEN_KEY, r.access_token);
      if (r.refresh_token) window.localStorage.setItem(REFRESH_KEY, r.refresh_token);
    }
    set({ user: r.user, token: r.access_token, loading: false });
  },

  logout: () => {
    if (DEV_BYPASS) {
      installDevToken();
      set({ user: DEV_SEED_USER, token: DEV_MAGIC_TOKEN, loading: false });
      return;
    }
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(TOKEN_KEY);
      window.localStorage.removeItem(REFRESH_KEY);
    }
    set({ user: null, token: null, loading: false });
  },
}));

export function useAuthSetup() {
  const hydrate = useAuth((s) => s.hydrate);
  const _clearUser = useAuth((s) => s._clearUser);

  useEffect(() => {
    hydrate();

    const onForcedLogout = () => _clearUser();
    const onStorage = (e: StorageEvent) => {
      if (e.key === TOKEN_KEY && !e.newValue) _clearUser();
    };

    window.addEventListener(AUTH_LOGOUT_EVENT, onForcedLogout);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(AUTH_LOGOUT_EVENT, onForcedLogout);
      window.removeEventListener("storage", onStorage);
    };
  }, [hydrate, _clearUser]);
}
