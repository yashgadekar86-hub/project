"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api } from "@/services/api";
import { FlaskConical, Loader2 } from "lucide-react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";

export default function BacktestPage() {
  const [form, setForm] = useState({
    symbol: "EURUSD", strategy: "trend_following", timeframe: "H1", initial_balance: 10000,
    start_date: "2024-01-01T00:00:00", end_date: "2025-01-01T00:00:00",
    commission_per_lot: 3, spread_pips: 1.2, slippage_pips: 0.5, risk_per_trade_pct: 0.5,
  });
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function run() {
    setBusy(true); setErr(null);
    try { setResult(await api.post("/api/v1/backtest", form)); } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2"><FlaskConical className="h-6 w-6 text-primary" /> Backtesting</h1>
        <p className="text-sm text-muted-foreground">Run historical backtests using the same strategies and risk engine used in live trading. Walk-forward testing is also available.</p>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-1">
          <CardHeader><CardTitle className="text-base">Configuration</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <div className="space-y-1.5"><Label>Symbol</Label><Input value={form.symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value })} /></div>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1.5"><Label>Strategy</Label>
                <select className="w-full h-9 rounded-md border border-input bg-background px-3 text-sm" value={form.strategy} onChange={(e) => setForm({ ...form, strategy: e.target.value })}>
                  <option value="trend_following">Trend Following</option>
                  <option value="breakout">Breakout</option>
                  <option value="pullback">Trend Pullback</option>
                  <option value="mean_reversion">Mean Reversion</option>
                  <option value="market_structure">Market Structure</option>
                </select>
              </div>
              <div className="space-y-1.5"><Label>Timeframe</Label>
                <select className="w-full h-9 rounded-md border border-input bg-background px-3 text-sm" value={form.timeframe} onChange={(e) => setForm({ ...form, timeframe: e.target.value })}>
                  {["M5", "M15", "M30", "H1", "H4", "D1"].map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1.5"><Label>Start</Label><Input type="datetime-local" value={form.start_date.slice(0, 16)} onChange={(e) => setForm({ ...form, start_date: e.target.value })} /></div>
              <div className="space-y-1.5"><Label>End</Label><Input type="datetime-local" value={form.end_date.slice(0, 16)} onChange={(e) => setForm({ ...form, end_date: e.target.value })} /></div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1.5"><Label>Initial Balance</Label><Input type="number" value={form.initial_balance} onChange={(e) => setForm({ ...form, initial_balance: +e.target.value })} /></div>
              <div className="space-y-1.5"><Label>Risk/Trade %</Label><Input type="number" step="0.1" value={form.risk_per_trade_pct} onChange={(e) => setForm({ ...form, risk_per_trade_pct: +e.target.value })} /></div>
              <div className="space-y-1.5"><Label>Commission/Lot</Label><Input type="number" step="0.1" value={form.commission_per_lot} onChange={(e) => setForm({ ...form, commission_per_lot: +e.target.value })} /></div>
              <div className="space-y-1.5"><Label>Spread (pips)</Label><Input type="number" step="0.1" value={form.spread_pips} onChange={(e) => setForm({ ...form, spread_pips: +e.target.value })} /></div>
              <div className="space-y-1.5"><Label>Slippage (pips)</Label><Input type="number" step="0.1" value={form.slippage_pips} onChange={(e) => setForm({ ...form, slippage_pips: +e.target.value })} /></div>
            </div>
            <Button onClick={run} disabled={busy} className="w-full">{busy ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : null} Run Backtest</Button>
            {err && <p className="text-sm text-destructive">{err}</p>}
            <div className="text-[11px] text-warning">Backtest results are hypothetical. Always walk-forward validate and paper-trade before considering live capital.</div>
          </CardContent>
        </Card>
        <Card className="lg:col-span-2">
          <CardHeader><CardTitle className="text-base">Equity Curve</CardTitle></CardHeader>
          <CardContent>
            {result?.equity_curve?.length ? (
              <ResponsiveContainer width="100%" height={320}>
                <LineChart data={result.equity_curve.map((p: any) => ({ time: p.time, equity: p.equity }))}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                  <XAxis dataKey="time" hide />
                  <YAxis domain={["auto", "auto"]} stroke="#6b7280" />
                  <Tooltip contentStyle={{ background: "#0b1220", border: "1px solid #1f2937" }} />
                  <Line type="monotone" dataKey="equity" stroke="hsl(199 89% 52%)" dot={false} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            ) : <div className="h-80 border border-dashed border-border rounded flex items-center justify-center text-sm text-muted-foreground">Configure and run a backtest to view the equity curve.</div>}
          </CardContent>
        </Card>
      </div>
      {result && (
        <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-3">
          <Stat label="Net Profit" value={`$${result.net_profit?.toFixed(2)}`} tone={result.net_profit > 0 ? "success" : "danger"} />
          <Stat label="Win Rate" value={`${result.win_rate?.toFixed(1)}%`} />
          <Stat label="Profit Factor" value={result.profit_factor?.toFixed(2)} />
          <Stat label="Max DD" value={`${result.max_drawdown_pct?.toFixed(2)}%`} tone="danger" />
          <Stat label="Sharpe" value={result.sharpe_ratio?.toFixed(2)} />
          <Stat label="Sortino" value={result.sortino_ratio?.toFixed(2)} />
          <Stat label="Trades" value={result.total_trades} />
        </div>
      )}
    </div>
  );
}
function Stat({ label, value, tone }: { label: string; value: any; tone?: "success" | "danger" }) {
  const cls = tone === "success" ? "text-success" : tone === "danger" ? "text-destructive" : "text-foreground";
  return (
    <div className="border border-border rounded-md p-3">
      <div className="stat-label">{label}</div>
      <div className={"stat-value mt-1 " + cls}>{value ?? "—"}</div>
    </div>
  );
}
