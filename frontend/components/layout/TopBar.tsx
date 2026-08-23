"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/hooks/useAuth";
import { api, type MT5Status } from "@/services/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Power, AlertTriangle, CircleDot, LogOut } from "lucide-react";
import { cn } from "@/lib/utils";
import { useRouter } from "next/navigation";
import Link from "next/link";

import { DEV_BYPASS } from "@/hooks/useAuth";

export function TopBar() {
  const { user, logout } = useAuth();
  const router = useRouter();
  const [mode, setMode] = useState<"backtest" | "paper" | "live">("paper");
  const [mt5, setMt5] = useState<MT5Status>({ connected: false });
  const [killswitch, setKillswitch] = useState(false);

  async function refresh() {
    try { setMt5(await api.get("/api/v1/mt5/status")); } catch {}
    try {
      const s = await api.get<any>("/api/v1/settings");
      setMode(s.trading_mode);
      setKillswitch(!!s.kill_switch_active);
    } catch {}
  }
  useEffect(() => { refresh(); const t = setInterval(refresh, 5000); return () => clearInterval(t); }, []);

  const modeBadge = (() => {
    if (mode === "live") return <Badge variant="green" className="gap-1.5"><span className="h-2 w-2 rounded-full bg-success animate-pulse-dot" /> LIVE</Badge>;
    if (mode === "backtest") return <Badge variant="blue" className="gap-1.5"><CircleDot className="h-3 w-3" /> BACKTEST</Badge>;
    return <Badge variant="yellow" className="gap-1.5"><CircleDot className="h-3 w-3" /> PAPER</Badge>;
  })();

  async function toggleKill() {
    if (!killswitch) {
      if (!confirm("EMERGENCY KILL SWITCH — disable all new trading and close paper positions?")) return;
      await api.post("/api/v1/system/kill-switch", { active: true, close_positions: true, confirmation: "CONFIRM", reason: "User activated from topbar" });
    } else {
      await api.post("/api/v1/system/kill-switch", { active: false, close_positions: false, confirmation: "CONFIRM", reason: "User deactivated" });
    }
    refresh();
  }

  return (
    <header className="h-16 border-b border-border bg-card flex items-center px-4 gap-4">
      <div className="flex items-center gap-3">
        {modeBadge}
        <Badge variant={mt5.connected ? "green" : "red"} className="gap-1.5">
          <span className={cn("h-2 w-2 rounded-full", mt5.connected ? "bg-success animate-pulse-dot" : "bg-destructive")} />
          MT5 {mt5.connected ? "Connected" : "Disconnected"}
        </Badge>
        {mt5.connected && (
          <div className="hidden md:flex items-center gap-3 text-xs text-muted-foreground mono">
            <span>{mt5.broker}</span>
            <span>·</span>
            <span>Acc {mt5.login}</span>
            <span>·</span>
            <span>Bal ${mt5.balance?.toFixed(2)}</span>
            <span>Eq ${mt5.equity?.toFixed(2)}</span>
          </div>
        )}
        {killswitch && (
          <Badge variant="red" className="animate-pulse gap-1.5"><AlertTriangle className="h-3 w-3" /> KILL SWITCH ACTIVE</Badge>
        )}
      </div>
      <div className="flex-1" />
      <Button variant="danger" size="sm" onClick={toggleKill} className="gap-1.5">
        <Power className="h-4 w-4" /> {killswitch ? "Deactivate Kill" : "STOP ALL TRADING"}
      </Button>
      <div className="flex items-center gap-2 pl-4 border-l border-border">
        <div className="text-right">
          <div className="text-sm font-medium">{user?.username || "—"}</div>
          <div className="text-[10px] uppercase text-muted-foreground">{user?.role}</div>
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => {
            if (DEV_BYPASS) {
              // Dev mode: logout is a no-op that just reloads dashboard;
              // store is never cleared so no loop is possible.
              window.location.href = "/dashboard";
              return;
            }
            logout();
            window.location.replace("/login");
          }}
          title={DEV_BYPASS ? "Dev mode — login disabled" : "Logout"}
        >
          <LogOut className="h-4 w-4" />
        </Button>
      </div>
    </header>
  );
}
