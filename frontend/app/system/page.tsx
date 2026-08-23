"use client";
import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { api } from "@/services/api";
import { Activity } from "lucide-react";

function tone(s: string) { return s === "HEALTHY" ? "green" : s === "WARNING" ? "yellow" : "red"; }

export default function SystemPage() {
  const [health, setHealth] = useState<any[]>([]);
  useEffect(() => {
    function load() { api.get("/api/v1/system/health").then((r) => setHealth(r.components)).catch(() => {}); }
    load(); const t = setInterval(load, 5000); return () => clearInterval(t);
  }, []);
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold flex items-center gap-2"><Activity className="h-6 w-6 text-primary" /> System Health</h1>
      <p className="text-sm text-muted-foreground">Real-time status of MT5, database, Redis, AI service, execution engine, market data, and host resources.</p>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {health.map((c) => (
          <Card key={c.component}>
            <CardHeader className="flex-row items-center justify-between">
              <CardTitle className="text-base capitalize">{c.component.replace(/_/g, " ")}</CardTitle>
              <Badge variant={tone(c.status)}>{c.status}</Badge>
            </CardHeader>
            <CardContent className="text-sm text-muted-foreground">
              {c.latency_ms != null && <div>Latency: {c.latency_ms?.toFixed?.(1) ?? c.latency_ms} ms</div>}
              {c.message && <div>{c.message}</div>}
              <div className="text-xs mt-1">Last heartbeat: {new Date(c.last_heartbeat).toLocaleString()}</div>
            </CardContent>
          </Card>
        ))}
        {health.length === 0 && <p className="text-sm text-muted-foreground">Loading health status...</p>}
      </div>
    </div>
  );
}
