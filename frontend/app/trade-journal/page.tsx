"use client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useEffect, useState } from "react";
import { api } from "@/services/api";
import { formatMoney, pnlColor, cn, formatPrice } from "@/lib/utils";
import { BookOpen } from "lucide-react";

export default function JournalPage() {
  const [trades, setTrades] = useState<any[]>([]);
  useEffect(() => { api.get("/api/v1/trade-journal").then(setTrades).catch(() => {}); }, []);
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2"><BookOpen className="h-6 w-6 text-primary" /> Trade Journal</h1>
        <p className="text-sm text-muted-foreground">Full audit trail of every signal, order, and result.</p>
      </div>
      <Card>
        <CardHeader><CardTitle className="text-base">Entries ({trades.length})</CardTitle></CardHeader>
        <CardContent>
          {trades.length === 0 ? <div className="text-sm text-muted-foreground">No journal entries yet. Trades and signals appear here automatically.</div> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-muted-foreground text-xs uppercase"><tr><th className="text-left py-2">Time</th><th className="text-left">Event</th><th className="text-left">Symbol</th><th className="text-left">Dir</th><th className="text-right">Entry</th><th className="text-right">SL</th><th className="text-right">TP</th><th className="text-right">Lot</th><th className="text-right">P/L</th><th className="text-right">R</th><th className="text-left">Reason</th></tr></thead>
                <tbody>
                  {trades.map((t, i) => (
                    <tr key={i} className="border-t border-border">
                      <td className="py-2 text-xs num">{t.timestamp ? new Date(t.timestamp).toLocaleString() : "—"}</td>
                      <td><Badge variant="muted">{t.event_type}</Badge></td>
                      <td className="font-medium">{t.symbol}</td>
                      <td>{t.direction ? <Badge variant={t.direction === "BUY" ? "green" : "red"}>{t.direction}</Badge> : "—"}</td>
                      <td className="text-right num">{formatPrice(t.entry)}</td>
                      <td className="text-right num text-destructive">{formatPrice(t.sl)}</td>
                      <td className="text-right num text-success">{formatPrice(t.tp)}</td>
                      <td className="text-right num">{t.lot ?? "—"}</td>
                      <td className={cn("text-right num font-semibold", pnlColor(t.pnl))}>{t.pnl != null ? formatMoney(t.pnl) : "—"}</td>
                      <td className="text-right num">{t.r_multiple?.toFixed?.(2) ?? "—"}</td>
                      <td className="text-xs text-muted-foreground max-w-[300px] truncate">{t.exit_reason || t.reason || "—"}</td>
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
