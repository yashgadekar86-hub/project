"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api, type Position } from "@/services/api";
import { formatMoney, formatPrice, pnlColor, cn } from "@/lib/utils";

export default function PositionsPage() {
  const [pos, setPos] = useState<Position[]>([]);

  async function load() { try { setPos(await api.get("/api/v1/positions")); } catch {} }
  useEffect(() => { load(); const t = setInterval(load, 2000); return () => clearInterval(t); }, []);

  async function close(ticket: number) {
    if (!confirm(`Close position ${ticket}?`)) return;
    try { await api.post(`/api/v1/orders/close/${ticket}`); load(); } catch (e: any) { alert(e.message); }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Open Positions</h1>
        <p className="text-sm text-muted-foreground">Real-time positions from MT5 and paper-trading engine.</p>
      </div>
      <Card>
        <CardHeader><CardTitle className="text-base">Positions ({pos.length})</CardTitle></CardHeader>
        <CardContent>
          {pos.length === 0 ? <div className="text-sm text-muted-foreground">No open positions.</div> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-muted-foreground text-xs uppercase tracking-wider">
                  <tr>
                    <th className="text-left py-2">Ticket</th><th className="text-left">Symbol</th><th className="text-left">Dir</th>
                    <th className="text-right">Lot</th><th className="text-right">Entry</th><th className="text-right">Price</th><th className="text-right">SL</th><th className="text-right">TP</th>
                    <th className="text-right">P/L</th><th className="text-right">R</th><th className="text-right">Duration</th><th className="text-left">Strategy</th><th className="text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {pos.map((p) => (
                    <tr key={p.ticket ?? `${p.symbol}-${p.entry_price}`} className="border-t border-border">
                      <td className="py-2 num">{p.ticket ?? "paper"}</td>
                      <td className="font-medium">{p.symbol}</td>
                      <td><Badge variant={p.direction === "BUY" ? "green" : "red"}>{p.direction}</Badge></td>
                      <td className="text-right num">{p.volume_lots.toFixed(2)}</td>
                      <td className="text-right num">{formatPrice(p.entry_price)}</td>
                      <td className="text-right num">{formatPrice(p.current_price)}</td>
                      <td className="text-right num text-destructive">{formatPrice(p.stop_loss)}</td>
                      <td className="text-right num text-success">{formatPrice(p.take_profit)}</td>
                      <td className={cn("text-right num font-semibold", pnlColor(p.pnl))}>{formatMoney(p.pnl)}</td>
                      <td className="text-right num">{p.r_multiple?.toFixed(2) ?? "—"}</td>
                      <td className="text-right num text-muted-foreground text-xs">{p.opened_at ? new Date(p.opened_at).toLocaleString() : "—"}</td>
                      <td className="text-xs text-muted-foreground">{p.strategy_name || "manual"}</td>
                      <td className="text-right"><Button size="sm" variant="destructive" onClick={() => close(p.ticket!)}>Close</Button></td>
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
