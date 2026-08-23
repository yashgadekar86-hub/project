"use client";

import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

export function StatCard({
  label, value, sub, tone = "default", icon,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "default" | "success" | "danger" | "warning";
  icon?: ReactNode;
}) {
  const toneCls = {
    default: "text-foreground",
    success: "text-success",
    danger: "text-destructive",
    warning: "text-warning",
  }[tone];
  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="stat-label truncate">{label}</div>
            <div className={cn("stat-value mt-1", toneCls)}>{value}</div>
            {sub && <div className="text-xs text-muted-foreground mt-1">{sub}</div>}
          </div>
          {icon && <div className="text-muted-foreground flex-shrink-0 mt-0.5">{icon}</div>}
        </div>
      </CardContent>
    </Card>
  );
}
