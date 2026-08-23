"use client";

import { useEffect, useState, useMemo } from "react";
import { useSearchParams } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { api, type SymbolInfo } from "@/services/api";
import { formatPrice } from "@/lib/utils";
import { PlayCircle, AlertTriangle } from "lucide-react";

export default function TradingPage() {
  const sp = useSearchParams();
  const initial = sp.get("symbol") || "EURUSD";
  const [symbols, setSymbols] = useState<SymbolInfo[]>([]);
  const [symbol, setSymbol] = useState<string>(initial);
  const [direction, setDirection] = useState<"BUY" | "SELL">("BUY");
  const [sl, setSl] = useState("");
  const [tp, setTp] = useState("");
  const [entry, setEntry] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ type: "ok" | "err"; text: string } | null>(null);

  useEffect(() => { api.get<SymbolInfo[]>("/api/v1/mt5/symbols").then(setSymbols).catch(() => {}); }, []);

  const spec = symbols.find((s) => s.name === symbol);
  const digits = spec?.digits ?? 5;

  async function execute(mode: "paper" | "live") {
    setBusy(true); setMsg(null);
    try {
      const r = await api.post("/api/v1/orders/execute", {
        symbol, direction,
        entry: entry ? parseFloat(entry) : undefined,
        stop_loss: parseFloat(sl), take_profit: parseFloat(tp),
        trade_mode: mode, confirm_live: mode === "live",
      });
      setMsg({ type: r.success ? "ok" : "err", text: r.success ? `Filled. Ticket: ${r.ticket}, Volume: ${r.volume_lots}` : (r.rejection_reason || "Validation failed") });
    } catch (e: any) { setMsg({ type: "err", text: e.message }); }
    finally { setBusy(false); }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2"><PlayCircle className="h-6 w-6 text-primary" /> Trading</h1>
        <p className="text-sm text-muted-foreground">Execute paper or live orders through the Risk Engine. Live trading requires explicit activation.</p>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-1">
          <CardHeader>
            <CardTitle className="text-base">Order Ticket</CardTitle>
            <CardDescription>All orders pass through 14 pre-trade checks before reaching MT5.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="space-y-1.5">
              <Label>Symbol</Label>
              <select className="w-full h-9 rounded-md border border-input bg-background px-3 text-sm" value={symbol} onChange={(e) => setSymbol(e.target.value)}>
                {(symbols.length ? symbols : ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "NZDUSD"].map((n) => ({ name: n }))).map((s: any) => (
                  <option key={s.name} value={s.name}>{s.name}</option>
                ))}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Button variant={direction === "BUY" ? "success" : "outline"} onClick={() => setDirection("BUY")} className="w-full">BUY</Button>
              <Button variant={direction === "SELL" ? "danger" : "outline"} onClick={() => setDirection("SELL")} className="w-full">SELL</Button>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <div><Label>Entry</Label><Input className="num" value={entry} onChange={(e) => setEntry(e.target.value)} placeholder="market" /></div>
              <div><Label>SL</Label><Input className="num" value={sl} onChange={(e) => setSl(e.target.value)} placeholder="required" /></div>
              <div><Label>TP</Label><Input className="num" value={tp} onChange={(e) => setTp(e.target.value)} placeholder="required" /></div>
            </div>
            <div className="text-xs text-muted-foreground">
              Lot size is calculated automatically from your configured risk-per-trade, SL distance, and symbol contract specification. The risk engine enforces your SL/TP and R:R constraints.
            </div>
            {msg && <div className={"text-sm " + (msg.type === "ok" ? "text-success" : "text-destructive")}>{msg.text}</div>}
            <div className="flex gap-2 pt-2">
              <Button variant="secondary" className="flex-1" disabled={busy} onClick={() => execute("paper")}>🟡 Paper Trade</Button>
              <Button variant="danger" className="flex-1" disabled={busy} onClick={() => { if (confirm("Live trade — real capital at risk. Confirm?")) execute("live"); }}>🟢 Execute Live</Button>
            </div>
            <div className="text-[11px] text-warning flex items-start gap-1.5 border-t border-border pt-3">
              <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
              Live trading must be explicitly enabled in Settings, and MT5 must be connected. The Risk Engine can reject any order regardless of AI signal.
            </div>
          </CardContent>
        </Card>
        <Card className="lg:col-span-2">
          <CardHeader><CardTitle className="text-base">{symbol} · Coming Soon</CardTitle></CardHeader>
          <CardContent>
            <div className="h-80 border border-dashed border-border rounded-md flex items-center justify-center text-sm text-muted-foreground">
              Chart view powered by TradingView Lightweight Charts — connected via WebSocket for real-time candles and indicator overlays.
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
