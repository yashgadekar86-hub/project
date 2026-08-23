"use client";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Cpu } from "lucide-react";

const MODELS = [
  { name: "Logistic Regression", type: "linear", use: "Baseline directional probability", status: "ACTIVE" },
  { name: "Random Forest", type: "ensemble", use: "Robust non-linear feature interactions", status: "ACTIVE" },
  { name: "Gradient Boosting", type: "ensemble", use: "Primary confidence calibration", status: "ACTIVE" },
  { name: "XGBoost", type: "ensemble", use: "High-performance walk-forward validated model", status: "OPTIONAL" },
  { name: "LSTM / Transformer", type: "sequence", use: "Only after rigorous out-of-sample validation", status: "EXPERIMENTAL" },
];

export default function AIModelsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2"><Cpu className="h-6 w-6 text-primary" /> AI Models</h1>
        <p className="text-sm text-muted-foreground">LLMs are used only for post-hoc explanations. Numerical signals come from quant features + traditional ML trained on walk-forward splits.</p>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {MODELS.map((m) => (
          <Card key={m.name}>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="text-base">{m.name}</CardTitle>
                <Badge variant={m.status === "ACTIVE" ? "green" : m.status === "EXPERIMENTAL" ? "yellow" : "muted"}>{m.status}</Badge>
              </div>
              <CardDescription>{m.type}</CardDescription>
            </CardHeader>
            <CardContent><p className="text-sm text-muted-foreground">{m.use}</p></CardContent>
          </Card>
        ))}
      </div>
      <Card>
        <CardHeader><CardTitle className="text-base">Training Pipeline Safeguards</CardTitle></CardHeader>
        <CardContent>
          <ul className="text-sm text-muted-foreground list-disc list-inside space-y-1">
            <li>Chronological train/validation/test splits — no shuffling.</li>
            <li>Walk-forward validation with rolling training windows.</li>
            <li>Leakage checks: no future data, no Survivorship bias, no look-ahead features.</li>
            <li>Feature importance + SHAP tracking per model version.</li>
            <li>Model registry with rollback and shadow deployment before live.</li>
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
