"use client";
import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { api } from "@/services/api";
import { Sparkles } from "lucide-react";

const DESCRIPTIONS: Record<string, { d: string; regime: string }> = {
  trend_following: { d: "EMA alignment (9/20/50) + ADX trend strength filter + structural confirmation.", regime: "Trending" },
  breakout: { d: "Previous-session high/low breakouts with volatility expansion and close confirmation.", regime: "Breakout" },
  pullback: { d: "Higher-timeframe trend + retracement to key EMA + momentum confirmation for re-entry.", regime: "Trending pullback" },
  mean_reversion: { d: "Range regime detection using ADX/BB width with RSI extremes at Bollinger Band tags.", regime: "Range" },
  market_structure: { d: "Break-of-Structure / Change-of-Character with retest of broken level as support/resistance.", regime: "Structure shift" },
};

export default function StrategiesPage() {
  const [strats, setStrats] = useState<any[]>([]);
  useEffect(() => { api.get("/api/v1/strategies").then(setStrats).catch(() => {}); }, []);
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2"><Sparkles className="h-6 w-6 text-primary" /> Strategies</h1>
        <p className="text-sm text-muted-foreground">Plugin-based strategy architecture. Each strategy produces direction, entry, SL/TP, confidence, and reasoning.</p>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {(strats.length ? strats : Object.keys(DESCRIPTIONS).map((n) => ({ name: n, display_name: n.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()), description: DESCRIPTIONS[n].d }))).map((s) => (
          <Card key={s.name}>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="text-base">{s.display_name || s.name}</CardTitle>
                <Badge variant="blue">{DESCRIPTIONS[s.name]?.regime || "—"}</Badge>
              </div>
              <CardDescription>{DESCRIPTIONS[s.name]?.d || s.description}</CardDescription>
            </CardHeader>
            <CardContent>
              <pre className="text-[11px] mono text-muted-foreground bg-background/40 border border-border rounded p-2 overflow-x-auto">{JSON.stringify(s.default_params || {}, null, 2)}</pre>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
