import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatPrice(v: number | null | undefined, digits = 5) {
  if (v == null || isNaN(v)) return "—";
  return v.toFixed(digits);
}
export function formatMoney(v: number | null | undefined, currency = "$") {
  if (v == null || isNaN(v)) return "—";
  const sign = v < 0 ? "-" : "";
  return `${sign}${currency}${Math.abs(v).toFixed(2)}`;
}
export function formatPct(v: number | null | undefined) {
  if (v == null || isNaN(v)) return "—";
  return `${v.toFixed(2)}%`;
}
export function pnlColor(v: number | null | undefined) {
  if (v == null) return "text-muted-foreground";
  if (v > 0) return "text-success";
  if (v < 0) return "text-destructive";
  return "text-muted-foreground";
}
