"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  LayoutDashboard, LineChart, Brain, PlayCircle, Briefcase, BookOpen, FlaskConical,
  Settings as SettingsIcon, Link2, Bell, Activity, ShieldAlert, Sparkles, Cpu,
} from "lucide-react";

const NAV = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/markets", label: "Markets", icon: LineChart },
  { href: "/ai-signals", label: "AI Signals", icon: Brain },
  { href: "/trading", label: "Trading", icon: PlayCircle },
  { href: "/positions", label: "Positions", icon: Briefcase },
  { href: "/trade-journal", label: "Trade Journal", icon: BookOpen },
  { href: "/backtesting", label: "Backtesting", icon: FlaskConical },
  { href: "/strategies", label: "Strategies", icon: Sparkles },
  { href: "/ai-models", label: "AI Models", icon: Cpu },
  { href: "/risk-management", label: "Risk", icon: ShieldAlert },
  { href: "/analytics", label: "Analytics", icon: Activity },
  { href: "/alerts", label: "Alerts", icon: Bell },
  { href: "/mt5-connection", label: "MT5 Connection", icon: Link2 },
  { href: "/system", label: "System", icon: Activity },
  { href: "/settings", label: "Settings", icon: SettingsIcon },
];

export function Sidebar() {
  const path = usePathname();
  return (
    <aside className="w-60 shrink-0 border-r border-border bg-card flex flex-col">
      <div className="h-16 flex items-center px-4 border-b border-border">
        <div className="flex items-center gap-2">
          <div className="h-8 w-8 rounded-md bg-primary/20 border border-primary/40 flex items-center justify-center">
            <Brain className="h-5 w-5 text-primary" />
          </div>
          <div>
            <div className="text-sm font-bold tracking-wide">AI Forex</div>
            <div className="text-[10px] uppercase tracking-widest text-muted-foreground">Command Center</div>
          </div>
        </div>
      </div>
      <nav className="flex-1 overflow-y-auto p-2 space-y-1">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = path === href || (href !== "/dashboard" && path?.startsWith(href));
          return (
            <Link key={href} href={href} className={cn(
              "flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors",
              active ? "bg-primary/10 text-primary border border-primary/30" : "text-muted-foreground hover:bg-accent hover:text-foreground"
            )}>
              <Icon className="h-4 w-4" />
              <span>{label}</span>
            </Link>
          );
        })}
      </nav>
      <div className="p-3 border-t border-border text-[11px] text-muted-foreground">
        <div className="uppercase tracking-widest text-[10px] mb-1">Disclaimer</div>
        <p>Trading FX involves substantial risk of loss. Backtested/AI performance does not guarantee future results.</p>
      </div>
    </aside>
  );
}
