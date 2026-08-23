"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { StatCard } from "@/components/dashboard/StatCard";
import { api, type Dashboard, type Position, type Signal } from "@/services/api";
import { cn, formatMoney, formatPct, formatPrice, pnlColor } from "@/lib/utils";
import { Brain, Briefcase, TrendingUp, TrendingDown, Activity, DollarSign, ShieldAlert, Gauge } from "lucide-react";

export default function DashboardPage() {
  const [dash, setDash] = useState<Dashboard | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [ticks, setTicks] = useState<Record<string, { bid: number; ask: number; spread_pips: number }>>({});

  async function load() {
    try { setDash(await api.get("/api/v1/dashboard")); } catch {}
    try { setPositions(await api.get("/api/v1/positions")); } catch {}
    try { setSignals(await api.get("/api/v1/signals")); } catch {}
    try {
      const syms = await api.get<any[]>("/api/v1/mt5/symbols");
      const majors = syms.filter((s) => s.is_major).slice(0, 10);
      const tickMap: Record<string, any> = {};
      setTicks((prev) => ({ ...prev, ...Object.fromEntries(majors.map((s) => [s.name, { bid: 0, ask: 0, spread_pips: 0 }])) }));
    } catch {}
  }
  useEffect(() => { load(); const t = setInterval(load, 3000); return () => clearInterval(t); }, []);

  useEffect(() => {
    const proto = typeof window !== "undefined" ? (window.location.protocol === "https:" ? "wss:" : "ws:") : "ws:";
    // In dev-bypass mode the token() helper in api.ts returns the magic
    // token; here we read the same value from localStorage if present,
    // otherwise fall back to the magic string so the WS handshake succeeds.
    const stored = typeof window !== "undefined" ? window.localStorage.getItem("aifx_token") : null;
    const DEV_MAGIC = "dev-bypass-aifx-demo-user";
    const isDevBypass = process.env.NEXT_PUBLIC_DEV_AUTH_BYPASS === "true";
    const token = stored || (isDevBypass ? DEV_MAGIC : "");
    if (!token) return;
    const ws = new WebSocket(`${proto}//${window.location.host}/api/v1/ws?token=${encodeURIComponent(token)}`);
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "tick" && msg.payload) {
          setTicks((prev) => ({ ...prev, [msg.payload.symbol]: msg.payload }));
        }
      } catch {}
    };
    ws.onerror = () => {}; // silent — WS is non-critical on the dashboard
    return () => { try { ws.close(); } catch {} };
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Dashboard</h1>
        <p className="text-sm text-muted-foreground">Account, risk, positions, and AI signal summary.</p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-3">
        <StatCard label="Balance" value={formatMoney(dash?.balance ?? 0)} icon={<DollarSign className="h-4 w-4" />} />
        <StatCard label="Equity" value={formatMoney(dash?.equity ?? 0)} icon={<Gauge />} />
        <StatCard label="Today P/L" value={formatMoney(dash?.today_pnl ?? 0)} tone={dash && dash.today_pnl > 0 ? "success" : dash && dash.today_pnl < 0 ? "danger" : "default"} icon={<TrendingUp />} />
        <StatCard label="Open P/L" value={formatMoney(dash?.open_pnl ?? 0)} tone={dash && dash.open_pnl > 0 ? "success" : dash && dash.open_pnl < 0 ? "danger" : "default"} icon={<Activity />} />
        <StatCard label="Drawdown" value={formatPct(dash?.drawdown_pct ?? 0)} tone={dash && dash.drawdown_pct > 5 ? "danger" : dash && dash.drawdown_pct > 2 ? "warning" : "default"} icon={<ShieldAlert />} />
        <StatCard label="Win Rate" value={formatPct(dash?.win_rate ?? 0)} icon={<TrendingUp />} />
        <StatCard label="Profit Factor" value={(dash?.profit_factor ?? 0).toFixed(2)} icon={<TrendingDown />} />
        <StatCard label="Active Trades" value={dash?.active_trades ?? 0} icon={<Briefcase />} />
        <StatCard label="AI Confidence" value={`${(dash?.ai_confidence_avg ?? 0).toFixed(0)}%`} icon={<Brain />} />
        <StatCard label="Risk Used" value={formatPct(dash?.risk_used_pct ?? 0)} tone={dash && dash.risk_used_pct > 70 ? "warning" : "default"} icon={<ShieldAlert />} />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <Card className="xl:col-span-2">
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle className="text-base">Live Market Watch</CardTitle>
            <Link href="/markets" className="text-xs text-primary hover:underline">View all</Link>
          </CardHeader>
          <CardContent>
            <div className="text-xs text-muted-foreground mb-2">Connect MT5 to stream live prices.</div>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
              {Object.entries(ticks).slice(0, 12).map(([sym, t]) => {
                const mid = t.bid && t.ask ? (t.bid + t.ask) / 2 : 0;
                return (
                  <div key={sym} className="border border-border rounded-md p-3 bg-background/40">
                    <div className="flex items-center justify-between">
                      <div className="font-semibold text-sm">{sym}</div>
                      <Badge variant={t.spread_pips > 2.5 ? "yellow" : "green"}>{t.spread_pips.toFixed(1)} pip</Badge>
                    </div>
                    <div className="num text-lg mt-1">{formatPrice(mid)}</div>
                    <div className="text-xs text-muted-foreground num mt-0.5">
                      B <span className="text-destructive">{formatPrice(t.bid)}</span> · A <span className="text-success">{formatPrice(t.ask)}</span>
                    </div>
                  </div>
                );
              })}
              {Object.keys(ticks).length === 0 && (
                <div className="col-span-full text-sm text-muted-foreground p-6 text-center border border-dashed border-border rounded-md">
                  No live ticks yet. Go to <Link href="/mt5-connection" className="text-primary underline">MT5 Connection</Link> to connect your account and start streaming.
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle className="text-base">AI Signals</CardTitle>
            <Link href="/ai-signals" className="text-xs text-primary hover:underline">Analyze</Link>
          </CardHeader>
          <CardContent className="space-y-2">
            {signals.length === 0 && (
              <div className="text-sm text-muted-foreground">No signals yet. Run an analysis to generate BUY / SELL / NO-TRADE decisions.</div>
            )}
            {signals.slice(0, 6).map((s) => (
              <div key={s.symbol} className="border border-border rounded-md p-3">
                <div className="flex items-center justify-between">
                  <div className="font-semibold text-sm">{s.symbol}</div>
                  <Badge variant={s.decision === "BUY" ? "green" : s.decision === "SELL" ? "red" : "muted"}>{s.decision}</Badge>
                </div>
                <div className="text-xs text-muted-foreground mt-1">
                  Confidence <span className="text-foreground font-medium">{s.confidence}%</span> · Regime <span className="text-foreground">{s.market_regime.replace(/_/g, " ")}</span>
                </div>
                {s.entry && (
                  <div className="text-xs num mt-1">
                    E {formatPrice(s.entry)} · SL {formatPrice(s.stop_loss)} · TP {formatPrice(s.take_profit)} · R:R {s.risk_reward?.toFixed(2)}
                  </div>
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle className="text-base">Open Positions</CardTitle>
          <Link href="/positions" className="text-xs text-primary hover:underline">Manage</Link>
        </CardHeader>
        <CardContent>
          {positions.length === 0 ? (
            <div className="text-sm text-muted-foreground">No open positions.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-muted-foreground text-xs uppercase tracking-wider">
                  <tr><th className="text-left py-2">Ticket</th><th className="text-left">Symbol</th><th className="text-left">Dir</th><th className="text-right">Lot</th><th className="text-right">Entry</th><th className="text-right">Price</th><th className="text-right">SL</th><th className="text-right">TP</th><th className="text-right">P/L</th><th className="text-right">R</th></tr>
                </thead>
                <tbody>
                  {positions.map((p) => (
                    <tr key={p.ticket ?? `${p.symbol}-${p.entry_price}`} className="border-t border-border">
                      <td className="py-2 num">{p.ticket ?? "paper"}</td>
                      <td className="font-medium">{p.symbol}</td>
                      <td><Badge variant={p.direction === "BUY" ? "green" : "red"}>{p.direction}</Badge></td>
                      <td className="text-right num">{p.volume_lots.toFixed(2)}</td>
                      <td className="text-right num">{formatPrice(p.entry_price)}</td>
                      <td className="text-right num">{formatPrice(p.current_price)}</td>
                      <td className="text-right num">{formatPrice(p.stop_loss)}</td>
                      <td className="text-right num">{formatPrice(p.take_profit)}</td>
                      <td className={cn("text-right num font-semibold", pnlColor(p.pnl))}>{formatMoney(p.pnl)}</td>
                      <td className="text-right num">{p.r_multiple?.toFixed(2) ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
