"use client";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
export const DEV_BYPASS = process.env.NEXT_PUBLIC_DEV_AUTH_BYPASS === "true";
const DEV_MAGIC_TOKEN = "dev-bypass-aifx-demo-user";

// Event for notifying the auth store about a hard 401 (token invalid).
export const AUTH_LOGOUT_EVENT = "aifx:auth:forced-logout";

// In dev-bypass we unconditionally seed localStorage with the magic token
// as early as possible (module load) so that even if a component fires a
// fetch BEFORE useAuthSetup() hydrates, the Authorization header is still
// correct. We also install a storage listener that re-seeds it if anything
// clears it.
if (typeof window !== "undefined" && DEV_BYPASS) {
  try {
    window.localStorage.setItem("aifx_token", DEV_MAGIC_TOKEN);
  } catch {}
  if (typeof window !== "undefined") {
    window.addEventListener("storage", (e) => {
      if (e.key === "aifx_token" && !e.newValue) {
        try { window.localStorage.setItem("aifx_token", DEV_MAGIC_TOKEN); } catch {}
      }
    });
  }
}

function token(): string | null {
  if (typeof window === "undefined") return null;
  if (DEV_BYPASS) return DEV_MAGIC_TOKEN;
  return window.localStorage.getItem("aifx_token");
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function request<T = any>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };
  const t = token();
  if (t) headers.Authorization = `Bearer ${t}`;

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    cache: "no-store",
    redirect: "follow",
  });

  if (res.status === 401) {
    // In dev-bypass mode a 401 should be impossible because the magic token
    // is accepted by the backend. It can only happen during a restart window
    // or similar transient glitch. We NEVER clear state, NEVER dispatch a
    // logout event (which would cause a loop), and return a generic error
    // that callers treat as a normal transient failure (they all catch and
    // swallow errors on polling loops anyway).
    if (DEV_BYPASS) {
      throw new ApiError("Temporarily unavailable — retrying", 401);
    }
    if (t && typeof window !== "undefined") {
      window.localStorage.removeItem("aifx_token");
      window.localStorage.removeItem("aifx_refresh");
      try { window.dispatchEvent(new CustomEvent(AUTH_LOGOUT_EVENT)); } catch {}
    }
    throw new ApiError("Your session has expired. Please sign in again.", 401);
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string"
        ? j.detail
        : (j.detail ? JSON.stringify(j.detail) : JSON.stringify(j));
    } catch {}
    throw new ApiError(msg, res.status);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export const api = {
  get: <T = any>(p: string) => request<T>(p),
  post: <T = any>(p: string, body?: any) =>
    request<T>(p, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  put: <T = any>(p: string, body?: any) =>
    request<T>(p, { method: "PUT", body: body ? JSON.stringify(body) : undefined }),
  delete: <T = any>(p: string) => request<T>(p, { method: "DELETE" }),
  setToken: (t: string) => {
    if (typeof window !== "undefined") window.localStorage.setItem("aifx_token", t);
  },
  clearToken: () => {
    if (typeof window !== "undefined") {
      window.localStorage.removeItem("aifx_token");
      window.localStorage.removeItem("aifx_refresh");
    }
  },
  hasToken: () => !!token(),
};

export type Signal = {
  symbol: string; direction: "BUY" | "SELL" | null; decision: "BUY" | "SELL" | "NO_TRADE";
  confidence: number; market_regime: string; entry: number | null; stop_loss: number | null; take_profit: number | null;
  risk_reward: number | null; reasoning: string[]; warnings: string[]; trade_allowed: boolean; block_reasons: string[];
  scores: Record<string, number>; timeframe_alignment: Record<string, string>; indicators: Record<string, any>;
  strategy_name: string | null; primary_timeframe: string;
  explanation_why: string[]; explanation_why_not: string[];
};

export type Position = {
  ticket: number | null; symbol: string; direction: "BUY" | "SELL"; volume_lots: number; entry_price: number;
  current_price: number | null; stop_loss: number | null; take_profit: number | null; pnl: number; pnl_pips: number;
  r_multiple: number | null; commission: number; swap: number; state: string; exit_reason: string | null;
  ai_confidence: number | null; strategy_name: string | null; opened_at: string | null;
};

export type Dashboard = {
  balance: number; equity: number; today_pnl: number; open_pnl: number; drawdown_pct: number;
  win_rate: number; profit_factor: number; active_trades: number; ai_confidence_avg: number; risk_used_pct: number;
};

export type MT5Status = {
  connected: boolean; broker?: string; server?: string; login?: number; balance?: number; equity?: number;
  margin_free?: number; margin_level?: number | null; leverage?: number; currency?: string; account_type?: string;
  symbols_count?: number;
  error?: string;
};

export type SymbolInfo = { name: string; digits?: number; point?: number; pip_size?: number; lot_min?: number; lot_max?: number; lot_step?: number; spread?: number; currency_base?: string; currency_profit?: string; is_major: boolean; };
