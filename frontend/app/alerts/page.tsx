"use client";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Bell } from "lucide-react";
export default function AlertsPage() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold flex items-center gap-2"><Bell className="h-6 w-6 text-primary" /> Alerts</h1>
      <p className="text-sm text-muted-foreground">Configure notifications for signals, executions, SL/TP hits, drawdowns, disconnects, and kill-switch events via Web, Email, Telegram, and Desktop push.</p>
      <Card><CardHeader><CardTitle>Channels</CardTitle></CardHeader><CardContent className="text-sm text-muted-foreground">Web notifications are enabled by default. Telegram and Email configuration is available through environment variables and the Settings page in production deployments.</CardContent></Card>
    </div>
  );
}
