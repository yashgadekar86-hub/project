"use client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from "recharts";
import { useEffect, useState } from "react";
import { api } from "@/services/api";
import { Activity } from "lucide-react";

export default function AnalyticsPage() {
  const [trades, setTrades] = useState<any[]>([]);
  useEffect(() => { api.get("/api/v1/trades").then(setTrades).catch(() => {}); }, []);
  const wins = trades.filter((t) => (t.net_pnl ?? t.pnl ?? 0) > 0).length;
  const losses = trades.length - wins;
  const byMonth: Record<string, number> = {};
  trades.forEach((t) => {
    const d = new Date(t.closed_at || t.closedAt || Date.now());
    const k = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    byMonth[k] = (byMonth[k] || 0) + (t.net_pnl ?? t.pnl ?? 0);
  });
  const monthData = Object.entries(byMonth).map(([m, p]) => ({ month: m, pnl: +p.toFixed(2) }));
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2"><Activity className="h-6 w-6 text-primary" /> Analytics</h1>
        <p className="text-sm text-muted-foreground">Performance breakdown across time, strategies, and symbols.</p>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <CardHeader><CardTitle className="text-base">Win / Loss Distribution</CardTitle></CardHeader>
          <CardContent>
            {trades.length === 0 ? <p className="text-sm text-muted-foreground">No closed trades yet.</p> : (
              <ResponsiveContainer width="100%" height={260}>
                <PieChart>
                  <Pie data={[{ name: "Wins", value: wins }, { name: "Losses", value: losses }]} dataKey="value" nameKey="name" outerRadius={90} label>
                    <Cell fill="hsl(142 71% 45%)" /><Cell fill="hsl(0 72% 51%)" />
                  </Pie>
                  <Tooltip contentStyle={{ background: "#0b1220", border: "1px solid #1f2937" }} />
                </PieChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-base">Monthly P/L</CardTitle></CardHeader>
          <CardContent>
            {monthData.length === 0 ? <p className="text-sm text-muted-foreground">No data yet.</p> : (
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={monthData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                  <XAxis dataKey="month" stroke="#6b7280" fontSize={11} />
                  <YAxis stroke="#6b7280" fontSize={11} />
                  <Tooltip contentStyle={{ background: "#0b1220", border: "1px solid #1f2937" }} />
                  <Bar dataKey="pnl" fill="hsl(199 89% 52%)" />
                </BarChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
