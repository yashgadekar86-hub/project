"use client";
import { cn } from "@/lib/utils";

type Variant = "green" | "red" | "yellow" | "blue" | "muted";
export function Badge({ variant = "muted", className, children }: { variant?: Variant; className?: string; children: React.ReactNode }) {
  const cls = {
    green: "bg-success/15 text-success border border-success/30",
    red: "bg-destructive/15 text-destructive border border-destructive/30",
    yellow: "bg-warning/15 text-warning border border-warning/30",
    blue: "bg-primary/15 text-primary border border-primary/30",
    muted: "bg-muted text-muted-foreground border border-border",
  }[variant];
  return <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold", cls, className)}>{children}</span>;
}
