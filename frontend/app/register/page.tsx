"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth, DEV_BYPASS } from "@/hooks/useAuth";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Brain, Loader2 } from "lucide-react";

export default function RegisterPage() {
  const { register } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [pw, setPw] = useState("");
  const [full, setFull] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Dev-bypass: bounce straight to dashboard.
  useEffect(() => {
    if (DEV_BYPASS) window.location.replace("/dashboard");
  }, []);

  if (DEV_BYPASS) {
    return (
      <div className="h-screen flex items-center justify-center bg-background">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null); setBusy(true);
    try { await register(email, username, pw, full); router.push("/mt5-connection"); }
    catch (e: any) { setErr(e.message || "Registration failed"); }
    finally { setBusy(false); }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <div className="mx-auto h-12 w-12 rounded-md bg-primary/20 border border-primary/40 flex items-center justify-center mb-2">
            <Brain className="h-7 w-7 text-primary" />
          </div>
          <CardTitle className="text-xl">Create your account</CardTitle>
          <CardDescription>Start in PAPER mode — never trade live until you are ready.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="space-y-4">
            <div className="space-y-1.5"><Label>Email</Label><Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></div>
            <div className="space-y-1.5"><Label>Username</Label><Input value={username} onChange={(e) => setUsername(e.target.value)} required minLength={3} /></div>
            <div className="space-y-1.5"><Label>Full name (optional)</Label><Input value={full} onChange={(e) => setFull(e.target.value)} /></div>
            <div className="space-y-1.5"><Label>Password</Label><Input type="password" value={pw} onChange={(e) => setPw(e.target.value)} required minLength={8} /></div>
            {err && <p className="text-sm text-destructive">{err}</p>}
            <Button type="submit" disabled={busy} className="w-full">{busy ? "Creating..." : "Create account"}</Button>
            <p className="text-sm text-muted-foreground text-center">
              Already have an account? <Link href="/login" className="text-primary hover:underline">Sign in</Link>
            </p>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
