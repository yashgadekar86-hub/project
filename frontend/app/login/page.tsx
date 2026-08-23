"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useAuth, DEV_BYPASS } from "@/hooks/useAuth";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Brain, Loader2 } from "lucide-react";

export default function LoginPage() {
  const { login, user } = useAuth();
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Dev-bypass: bounce straight to dashboard immediately.
  useEffect(() => {
    if (DEV_BYPASS) {
      window.location.replace("/dashboard");
    }
  }, []);

  useEffect(() => {
    if (DEV_BYPASS) return;
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    if (params.get("reason") === "expired") {
      setErr("Your session expired. Please sign in again.");
    }
    window.history.replaceState({}, "", "/login");
  }, []);

  useEffect(() => {
    if (DEV_BYPASS) return;
    if (user) window.location.replace("/dashboard");
  }, [user]);

  if (DEV_BYPASS) {
    return (
      <div className="h-screen flex items-center justify-center bg-background">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      await login(email.trim(), pw);
      window.location.href = "/dashboard";
    } catch (e: any) {
      setErr(e?.message || "Login failed");
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4 bg-background">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <div className="mx-auto h-12 w-12 rounded-md bg-primary/20 border border-primary/40 flex items-center justify-center mb-2">
            <Brain className="h-7 w-7 text-primary" />
          </div>
          <CardTitle className="text-xl">AI Forex Command Center</CardTitle>
          <CardDescription>Sign in to your trading terminal</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="email">Email or Username</Label>
              <Input id="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" required autoFocus autoComplete="username" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="pw">Password</Label>
              <Input id="pw" type="password" value={pw} onChange={(e) => setPw(e.target.value)} placeholder="••••••••" required minLength={8} autoComplete="current-password" />
            </div>
            {err && <p className="text-sm text-destructive">{err}</p>}
            <Button type="submit" disabled={busy} className="w-full">{busy ? "Signing in..." : "Sign in"}</Button>
            <p className="text-sm text-muted-foreground text-center">
              Do not have an account? <Link href="/register" className="text-primary hover:underline">Create one</Link>
            </p>
            <p className="text-[11px] text-muted-foreground text-center">
              Trading FX involves substantial risk. This software defaults to PAPER mode; live trading requires explicit activation.
            </p>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
