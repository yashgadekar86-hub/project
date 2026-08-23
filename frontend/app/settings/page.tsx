"use client";
import { redirect } from "next/navigation";
import Link from "next/link";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Settings as SettingsIcon } from "lucide-react";

export default function SettingsPage() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold flex items-center gap-2"><SettingsIcon className="h-6 w-6 text-primary" /> Settings</h1>
      <p className="text-sm text-muted-foreground">Risk controls, strategy enablement, sessions, alerts, and notification channels.</p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Link href="/risk-management"><Card className="hover:border-primary/50 cursor-pointer transition-colors"><CardHeader><CardTitle className="text-base">Risk Management</CardTitle><CardDescription>Position sizing, daily loss limits, drawdown protection, spread filters.</CardDescription></CardHeader></Card></Link>
        <Link href="/mt5-connection"><Card className="hover:border-primary/50 cursor-pointer transition-colors"><CardHeader><CardTitle className="text-base">MT5 Connection</CardTitle><CardDescription>Brokers, accounts, credentials, and terminal path.</CardDescription></CardHeader></Card></Link>
        <Link href="/alerts"><Card className="hover:border-primary/50 cursor-pointer transition-colors"><CardHeader><CardTitle className="text-base">Alerts</CardTitle><CardDescription>Web, email, Telegram, desktop notifications.</CardDescription></CardHeader></Card></Link>
        <Link href="/strategies"><Card className="hover:border-primary/50 cursor-pointer transition-colors"><CardHeader><CardTitle className="text-base">Strategies</CardTitle><CardDescription>Enable/disable strategies and adjust parameters.</CardDescription></CardHeader></Card></Link>
        <Link href="/system"><Card className="hover:border-primary/50 cursor-pointer transition-colors"><CardHeader><CardTitle className="text-base">System Health</CardTitle><CardDescription>Monitor services, latency, and MT5 connectivity.</CardDescription></CardHeader></Card></Link>
      </div>
    </div>
  );
}
