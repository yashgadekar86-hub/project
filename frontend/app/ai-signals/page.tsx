"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api, type Signal, type SymbolInfo } from "@/services/api";
import { formatPrice } from "@/lib/utils";
import { Brain, Loader2, AlertCircle, CheckCircle2, XCircle } from "lucide-react";
import Link from "next/link";

export default function AISignalsPage() {
  const [symbols, setSymbols] = useState<SymbolInfo[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.get<SymbolInfo[]>("/api/v1/mt5/symbols").then((s) => {
      setSymbols(s);
      const majors = s.filter((x) => x.is_major).slice(0, 8).map((x) => x.name);
      setSelected(majors);
    }).catch(() => {});
  }, []);

  async function analyze() {
    setBusy(true); setErr(null);
    try {
      const r = await api.post<Signal[]>("/api/v1/signals/analyze", { symbols: selected });
      setSignals(r);
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  }

  function toggle(sym: string) {
    setSelected((prev) => prev.includes(sym) ? prev.filter((s) => s !== sym) : [...prev, sym]);
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2"><Brain className="h-6 w-6 text-primary" /> AI Signals</h1>
          <p className="text-sm text-muted-foreground">Multi-timeframe analysis combining quantitative indicators, ML confidence, and strict risk gating. <strong>NO TRADE</strong> is a first-class decision.</p>
        </div>
        <Button onClick={analyze} disabled={busy || selected.length === 0}>
          {busy ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Brain className="h-4 w-4 mr-2" />} Analyze {selected.length} symbols
        </Button>
      </div>

      <Card>
        <CardHeader><CardTitle className="text-base">Watchlist</CardTitle></CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-2">
            {symbols.map((s) => (
              <button key={s.name} onClick={() => toggle(s.name)} className={
                "px-3 py-1 rounded-full text-xs border transition-colors " +
                (selected.includes(s.name) ? "bg-primary/20 border-primary/50 text-primary" : "border-border bg-background hover:bg-accent text-muted-foreground")
              }>{s.name}</button>
            ))}
            {symbols.length === 0 && <p className="text-sm text-muted-foreground">No symbols — connect MT5 first.</p>}
          </div>
        </CardContent>
      </Card>

      {err && <div className="text-sm text-destructive flex items-start gap-2"><AlertCircle className="h-4 w-4 mt-0.5" />{err}</div>}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {signals.map((s) => (
          <Card key={s.symbol} className={s.trade_allowed ? "border-success/30" : s.decision === "NO_TRADE" ? "border-border" : "border-warning/30"}>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="flex items-center gap-2">
                  {s.symbol}
                  <Badge variant={s.decision === "BUY" ? "green" : s.decision === "SELL" ? "red" : "muted"}>
                    {s.direction === "BUY" ? "🟢 BUY" : s.direction === "SELL" ? "🔴 SELL" : "⚪ NO TRADE"}
                  </Badge>
                </CardTitle>
                <div className="text-right">
                  <div className="text-2xl font-bold mono">{s.confidence}%</div>
                  <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Confidence</div>
                </div>
              </div>
              <div className="flex flex-wrap gap-1 mt-2">
                <Badge variant="blue">{s.market_regime.replace(/_/g, " ")}</Badge>
                {s.strategy_name && <Badge variant="yellow">{s.strategy_name.replace(/_/g, " ")}</Badge>}
                {s.primary_timeframe && <Badge variant="muted">TF {s.primary_timeframe}</Badge>}
                {s.trade_allowed ? <Badge variant="green"><CheckCircle2 className="h-3 w-3 mr-1" /> Trade Allowed</Badge> : <Badge variant="red"><XCircle className="h-3 w-3 mr-1" /> Blocked</Badge>}
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              {s.entry && (
                <div className="grid grid-cols-4 gap-2 text-sm">
                  <Stat k="Entry" v={formatPrice(s.entry)} />
                  <Stat k="SL" v={formatPrice(s.stop_loss)} tone="danger" />
                  <Stat k="TP" v={formatPrice(s.take_profit)} tone="success" />
                  <Stat k="R:R" v={s.risk_reward ? `1:${s.risk_reward.toFixed(2)}` : "—"} />
                </div>
              )}

              <div>
                <div className="text-xs uppercase text-muted-foreground tracking-wider mb-1">Score Breakdown</div>
                <div className="grid grid-cols-4 gap-2 text-xs">
                  {Object.entries(s.scores || {}).filter(([k]) => k !== "overall" && k !== "grade").map(([k, v]) => (
                    <div key={k} className="bg-muted/50 rounded p-1.5">
                      <div className="text-muted-foreground capitalize">{k.replace("_", " ")}</div>
                      <div className="font-semibold mono">{v}/100</div>
                    </div>
                  ))}
                </div>
                {s.scores.overall != null && (
                  <div className="mt-2">
                    <div className="flex justify-between text-xs"><span>Overall</span><span className="mono font-semibold">{s.scores.overall}/100 — {grade(s.scores.overall)}</span></div>
                    <div className="h-2 rounded-full bg-muted overflow-hidden mt-1">
                      <div className="h-full bg-primary" style={{ width: `${s.scores.overall}%` }} />
                    </div>
                  </div>
                )}
              </div>

              {s.reasoning.length > 0 && (
                <div>
                  <div className="text-xs uppercase text-muted-foreground tracking-wider mb-1">Why {s.decision}?</div>
                  <ul className="text-sm space-y-0.5 list-disc list-inside">
                    {s.explanation_why.slice(0, 5).map((r, i) => <li key={i}>{r}</li>)}
                  </ul>
                </div>
              )}
              {s.warnings.length > 0 && (
                <div>
                  <div className="text-xs uppercase text-warning tracking-wider mb-1">Warnings</div>
                  <ul className="text-sm space-y-0.5 list-disc list-inside">
                    {s.warnings.map((w, i) => <li key={i} className="text-warning/90">{w}</li>)}
                  </ul>
                </div>
              )}
              {s.block_reasons.length > 0 && (
                <div>
                  <div className="text-xs uppercase text-destructive tracking-wider mb-1">Block Reasons</div>
                  <ul className="text-sm space-y-0.5 list-disc list-inside text-destructive/90">
                    {s.block_reasons.map((b, i) => <li key={i}>{b.replace(/_/g, " ")}</li>)}
                  </ul>
                </div>
              )}

              <div className="flex gap-2 pt-2">
                <Link href={`/trading?symbol=${s.symbol}`}><Button size="sm" variant="outline">Trade {s.symbol}</Button></Link>
              </div>
            </CardContent>
          </Card>
        ))}
        {signals.length === 0 && !busy && (
          <Card className="col-span-full">
            <CardContent className="p-12 text-center text-muted-foreground">
              <Brain className="h-10 w-10 mx-auto mb-2 text-primary/60" />
              Select symbols and click <strong>Analyze</strong> to generate AI signals. No trades are placed without your explicit confirmation.
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}

function Stat({ k, v, tone }: { k: string; v: React.ReactNode; tone?: "danger" | "success" }) {
  const cls = tone === "danger" ? "text-destructive" : tone === "success" ? "text-success" : "text-foreground";
  return (
    <div className="bg-muted/40 rounded p-2">
      <div className="text-[10px] uppercase text-muted-foreground">{k}</div>
      <div className={"num font-semibold " + cls}>{v}</div>
    </div>
  );
}
function grade(score: number) {
  if (score >= 90) return "Exceptional";
  if (score >= 80) return "Strong";
  if (score >= 70) return "Valid";
  if (score >= 60) return "Weak";
  return "NO TRADE";
}
