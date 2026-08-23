"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription, CardFooter } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { api } from "@/services/api";
import { ShieldAlert, Save } from "lucide-react";

export default function RiskPage() {
  const [s, setS] = useState<any>(null);
  const [msg, setMsg] = useState<string | null>(null);

  async function load() { try { setS(await api.get("/api/v1/settings")); } catch {} }
  useEffect(() => { load(); }, []);

  async function save() {
    try { await api.put("/api/v1/settings", s); setMsg("Saved."); setTimeout(() => setMsg(null), 2000); } catch (e: any) { setMsg(e.message); }
  }

  if (!s) return <div className="text-sm text-muted-foreground">Loading risk settings...</div>;

  const num = (k: string) => <Input type="number" step="any" value={s[k] ?? ""} onChange={(e) => setS({ ...s, [k]: parseFloat(e.target.value) })} />;
  const bool = (k: string) => <Switch checked={!!s[k]} onCheckedChange={(v) => setS({ ...s, [k]: v })} />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2"><ShieldAlert className="h-6 w-6 text-warning" /> Risk Management</h1>
        <p className="text-sm text-muted-foreground">These settings control the non-bypassable Risk Engine. Conservative defaults protect your capital.</p>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Card>
          <CardHeader><CardTitle className="text-base">Position & Daily Risk</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <Field label="Risk per trade (%)" htmlFor="risk_per_trade_pct">{num("risk_per_trade_pct")}</Field>
            <Field label="Max daily loss (%)" htmlFor="max_daily_loss_pct">{num("max_daily_loss_pct")}</Field>
            <Field label="Max drawdown (%)" htmlFor="max_drawdown_pct">{num("max_drawdown_pct")}</Field>
            <Field label="Max simultaneous trades" htmlFor="max_simultaneous_trades">{num("max_simultaneous_trades")}</Field>
            <Field label="Max lot size" htmlFor="max_lot_size">{num("max_lot_size")}</Field>
            <Field label="Daily trade limit" htmlFor="daily_trade_limit">{num("daily_trade_limit")}</Field>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-base">Entry Constraints</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <Field label="Min AI confidence (0-100)" htmlFor="min_ai_confidence">{num("min_ai_confidence")}</Field>
            <Field label="Min risk/reward" htmlFor="min_risk_reward">{num("min_risk_reward")}</Field>
            <Field label="Max spread (pips, majors)" htmlFor="max_spread_pips_majors">{num("max_spread_pips_majors")}</Field>
            <Field label="Max slippage (pips)" htmlFor="max_slippage_pips">{num("max_slippage_pips")}</Field>
            <Field label="Max consecutive losses" htmlFor="max_consecutive_losses">{num("max_consecutive_losses")}</Field>
            <Field label="Cooldown after losses (min)" htmlFor="cooldown_after_losses_minutes">{num("cooldown_after_losses_minutes")}</Field>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-base">Trade Management Toggles</CardTitle><CardDescription>Each management feature is individually switchable.</CardDescription></CardHeader>
          <CardContent className="space-y-3">
            <Toggle label="Break-even" description="Move SL to entry when R:R reaches 1.">{bool("break_even_enabled")}</Toggle>
            <Toggle label="Trailing stop" description="Trail SL as price moves in your favor.">{bool("trailing_stop_enabled")}</Toggle>
            <Toggle label="ATR trailing stop" description="Volatility-adaptive trailing stop.">{bool("atr_trailing_stop_enabled")}</Toggle>
            <Toggle label="Partial close" description="Bank partial profits at TP1 and let a runner go.">{bool("partial_close_enabled")}</Toggle>
            <Toggle label="News filter" description="Block new trades around high-impact news (requires news source).">{bool("news_filter_enabled")}</Toggle>
            <Toggle label="Auto-trade (AI executes directly)" description="If disabled, all signals are manual-only.">{bool("auto_trade_enabled")}</Toggle>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-base">Trading Mode</CardTitle><CardDescription>PAPER by default. Live requires explicit activation per MT5 account.</CardDescription></CardHeader>
          <CardContent className="space-y-3">
            <Field label="Mode" htmlFor="trading_mode">
              <select className="w-full h-9 rounded-md border border-input bg-background px-3 text-sm" value={s.trading_mode} onChange={(e) => setS({ ...s, trading_mode: e.target.value })}>
                <option value="paper">🟡 Paper</option>
                <option value="backtest">🔵 Backtest</option>
                <option value="live">🟢 Live (requires activation)</option>
              </select>
            </Field>
            <div className="text-xs text-muted-foreground">Changing to live requires that you have explicitly activated live trading on your MT5 account. The system will still reject trades if any risk check fails.</div>
          </CardContent>
        </Card>
      </div>
      <div className="flex items-center gap-3">
        <Button onClick={save}><Save className="h-4 w-4 mr-2" /> Save Settings</Button>
        {msg && <span className="text-sm text-success">{msg}</span>}
      </div>
    </div>
  );
}

function Field({ label, htmlFor, children }: { label: string; htmlFor: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-2 items-center gap-3">
      <Label htmlFor={htmlFor} className="text-sm">{label}</Label>
      <div>{children}</div>
    </div>
  );
}
function Toggle({ label, description, children }: { label: string; description?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-border p-3">
      <div>
        <div className="text-sm font-medium">{label}</div>
        {description && <div className="text-xs text-muted-foreground">{description}</div>}
      </div>
      {children}
    </div>
  );
}
