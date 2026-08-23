"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { api, type SymbolInfo } from "@/services/api";
import { formatPrice } from "@/lib/utils";

type TickUpdate = { symbol: string; bid: number; ask: number; spread_pips: number; session?: string[] };

export default function MarketsPage() {
  const [symbols, setSymbols] = useState<SymbolInfo[]>([]);
  const [ticks, setTicks] = useState<Record<string, TickUpdate>>({});

  useEffect(() => {
    api.get<SymbolInfo[]>("/api/v1/mt5/symbols").then(setSymbols).catch(() => {});
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const token = localStorage.getItem("aifx_token");
    const ws = new WebSocket(`${proto}//${window.location.host}/ws?token=${token}`);
    ws.onopen = () => ws.send(JSON.stringify({ action: "subscribe", topic: "broadcast" }));
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "tick" && msg.payload) setTicks((prev) => ({ ...prev, [msg.payload.symbol]: msg.payload }));
      } catch {}
    };
    return () => ws.close();
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Markets</h1>
        <p className="text-sm text-muted-foreground">Real-time prices and conditions from MT5.</p>
      </div>

      <Card>
        <CardHeader><CardTitle className="text-base">Live Market Panel</CardTitle></CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-muted-foreground text-xs uppercase tracking-wider">
                <tr>
                  <th className="text-left py-2">Symbol</th>
                  <th className="text-left">Type</th>
                  <th className="text-right">Bid</th>
                  <th className="text-right">Ask</th>
                  <th className="text-right">Spread</th>
                  <th className="text-right">Digits</th>
                  <th className="text-right">Lot Min</th>
                  <th className="text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {symbols.map((s) => {
                  const t = ticks[s.name];
                  const bid = t?.bid ?? 0;
                  const ask = t?.ask ?? 0;
                  return (
                    <tr key={s.name} className="border-t border-border hover:bg-accent/40">
                      <td className="py-2 font-medium">{s.name}</td>
                      <td>{s.is_major ? <Badge variant="blue">Major</Badge> : <Badge variant="muted">—</Badge>}</td>
                      <td className="text-right num text-destructive">{bid ? formatPrice(bid, s.digits ?? 5) : "—"}</td>
                      <td className="text-right num text-success">{ask ? formatPrice(ask, s.digits ?? 5) : "—"}</td>
                      <td className="text-right num">{t ? <Badge variant={t.spread_pips > 3 ? "yellow" : "green"}>{t.spread_pips.toFixed(1)}</Badge> : "—"}</td>
                      <td className="text-right num">{s.digits ?? "—"}</td>
                      <td className="text-right num">{s.lot_min ?? "—"}</td>
                      <td className="text-right">
                        <Link href={`/trading?symbol=${s.name}`} className="text-xs text-primary hover:underline">Trade →</Link>
                      </td>
                    </tr>
                  );
                })}
                {symbols.length === 0 && (
                  <tr><td colSpan={8} className="py-6 text-center text-muted-foreground">No symbols loaded. Connect MT5 to populate this list.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
