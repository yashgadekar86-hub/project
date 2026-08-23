"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api, type MT5Status, type SymbolInfo } from "@/services/api";
import { formatMoney } from "@/lib/utils";
import { AlertCircle, CheckCircle2, Link2, Loader2, Info } from "lucide-react";

export default function MT5Page() {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [server, setServer] = useState("");
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [status, setStatus] = useState<MT5Status | null>(null);
  const [symbols, setSymbols] = useState<SymbolInfo[]>([]);

  async function load() {
    try {
      const s = await api.get<MT5Status>("/api/v1/mt5/status");
      setStatus(s);
    } catch {
      // Background polling failure — don't spam a red banner over the page.
      // The MT5 Disconnected badge in the TopBar already reflects state.
    }
    try {
      setSymbols(await api.get<SymbolInfo[]>("/api/v1/mt5/symbols"));
    } catch {
      // symbols endpoint may 404/empty — ignore
    }
  }
  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  // Detect Linux sandbox (browser user agent is not reliable for server OS, but we can detect the
  // specific MT5-unavailable message after first connect attempt; also show a permanent banner
  // in dev preview environments which are Linux-based).
  const isPreviewEnv = typeof window !== "undefined" &&
    (window.location.hostname.endsWith(".e2b.app") ||
     window.location.hostname.includes("sandbox") ||
     window.location.hostname === "localhost" ||
     window.location.hostname.startsWith("127."));

  async function connect(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null); setInfo(null);
    try {
      const result = await api.post<MT5Status>("/api/v1/mt5/connect", {
        login: Number(login), password, server, terminal_path: path || undefined, label: `${server}-${login}`,
      });
      setStatus(result);
      setInfo(`Connected to ${result.broker} — ${result.symbols_count} symbols detected.`);
      await load();
    } catch (e: any) {
      const msg: string = e?.message || "Connection failed";
      setErr(msg);
    } finally { setBusy(false); }
  }
  async function disconnect() {
    try { await api.post("/api/v1/mt5/disconnect"); } catch {}
    load();
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">MT5 Connection</h1>
        <p className="text-sm text-muted-foreground">Connect to your MetaTrader 5 terminal. FortressFX and any other MT5-compatible broker are supported — the platform auto-detects the broker, symbols, and contract specs.</p>
      </div>

      {isPreviewEnv && !status?.connected && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 flex gap-3">
          <Info className="h-5 w-5 text-amber-400 flex-shrink-0 mt-0.5" />
          <div className="text-sm text-amber-100 space-y-2">
            <p className="font-semibold text-amber-50">You are viewing a cloud-hosted preview — MT5 connection is disabled here</p>
            <p>
              Your FortressFX MT5 terminal is running on <strong>your Windows PC</strong>, but this website you're looking
              at right now is served from a Linux cloud sandbox. MetaTrader's Python API talks to MT5 via a
              local Windows DLL — it cannot reach across the internet to your desktop. So the
              <strong> Connect</strong> button below will always fail from this preview.
            </p>
            <p>
              <strong>To connect FortressFX for real:</strong> clone this repo onto your Windows PC,
              open MT5 and log into account <code>70177766</code> on <code>FortressFX-Trade</code>, then run
              <code className="mx-1 px-1 py-0.5 rounded bg-black/40">pip install MetaTrader5</code> and
              <code className="mx-1 px-1 py-0.5 rounded bg-black/40">uvicorn backend.api.main:app --host 0.0.0.0 --port 8000</code>
              on that same Windows machine. The frontend (running locally or pointed at your Windows IP)
              will then connect, pull your balance, symbols, and start streaming ticks.
            </p>
            <p className="text-amber-200/80">
              Everything else in this preview — the dashboard, AI signals, backtesting, risk engine,
              paper trading, analytics — works without MT5. Feel free to click around.
            </p>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><Link2 className="h-5 w-5 text-primary" /> Connect to MT5</CardTitle>
            <CardDescription>
              Credentials are encrypted at rest (Fernet) and never stored in the browser. MT5 must be installed
              on the same machine running the backend.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={connect} className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5"><Label>Login (account number)</Label><Input value={login} onChange={(e) => setLogin(e.target.value)} placeholder="12345678" required type="text" inputMode="numeric" /></div>
                <div className="space-y-1.5"><Label>Server</Label><Input value={server} onChange={(e) => setServer(e.target.value)} placeholder="FortressFX-Live" required /></div>
              </div>
              <div className="space-y-1.5"><Label>Password</Label><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="off" /></div>
              <div className="space-y-1.5"><Label>Terminal path (optional — auto-detected on Windows)</Label><Input value={path} onChange={(e) => setPath(e.target.value)} placeholder="C:\Program Files\MetaTrader 5\terminal64.exe" /></div>
              {err && (
                <div className="text-sm text-destructive flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3">
                  <AlertCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
                  <span className="whitespace-pre-wrap">{err}</span>
                </div>
              )}
              {info && (
                <div className="text-sm text-emerald-300 flex items-start gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/10 p-3">
                  <CheckCircle2 className="h-4 w-4 mt-0.5 flex-shrink-0" />
                  <span>{info}</span>
                </div>
              )}
              <div className="flex gap-2">
                <Button type="submit" disabled={busy}>
                  {busy ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Link2 className="h-4 w-4 mr-2" />}
                  {busy ? "Connecting..." : "Connect"}
                </Button>
                {status?.connected && <Button variant="outline" type="button" onClick={disconnect}>Disconnect</Button>}
              </div>
            </form>
            <div className="mt-4 text-xs text-muted-foreground border-t border-border pt-4 space-y-2">
              <p><strong>FortressFX users:</strong> enter the exact server name as it appears in your MT5 terminal (e.g. <code>FortressFX-Live</code>). Symbol suffixes and contract specs are auto-detected — nothing is hard-coded.</p>
              <p><strong>Other brokers:</strong> any MT5 broker works — IC Markets, Pepperstone, Exness, etc. Use the login/server/password from your broker.</p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Account Status</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center gap-2">
              <span className="stat-label">Status</span>
              <Badge variant={status?.connected ? "green" : "red"}>{status?.connected ? "Connected" : "Disconnected"}</Badge>
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
              <Row k="Broker" v={status?.broker} />
              <Row k="Server" v={status?.server} />
              <Row k="Account" v={status?.login} />
              <Row k="Type" v={status?.account_type} />
              <Row k="Balance" v={status?.balance != null ? formatMoney(status.balance, status.currency === "USD" ? "$" : (status.currency || "")) : "—"} />
              <Row k="Equity" v={status?.equity != null ? formatMoney(status.equity, "$") : "—"} />
              <Row k="Free Margin" v={status?.margin_free != null ? formatMoney(status.margin_free, "$") : "—"} />
              <Row k="Margin Level" v={status?.margin_level != null ? `${status.margin_level.toFixed(2)}%` : "—"} />
              <Row k="Leverage" v={status?.leverage ? `1:${status.leverage}` : "—"} />
              <Row k="Currency" v={status?.currency} />
              <Row k="Symbols" v={status?.symbols_count} />
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader><CardTitle>Detected Symbols ({symbols.length})</CardTitle></CardHeader>
        <CardContent>
          {symbols.length === 0 ? (
            <div className="text-sm text-muted-foreground">Connect MT5 to auto-discover available symbols and contract specifications.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-muted-foreground text-xs uppercase">
                  <tr><th className="text-left py-2">Symbol</th><th className="text-left">Type</th><th className="text-right">Digits</th><th className="text-right">Pip Size</th><th className="text-right">Lot Min</th><th className="text-right">Lot Max</th><th className="text-right">Lot Step</th><th className="text-right">Spread</th></tr>
                </thead>
                <tbody>
                  {symbols.map((s) => (
                    <tr key={s.name} className="border-t border-border">
                      <td className="py-2 font-medium">{s.name}</td><td>{s.is_major ? <Badge variant="blue">Major</Badge> : <Badge variant="muted">—</Badge>}</td>
                      <td className="text-right num">{s.digits}</td><td className="text-right num">{s.pip_size?.toFixed(s.digits ?? 5)}</td>
                      <td className="text-right num">{s.lot_min}</td><td className="text-right num">{s.lot_max}</td><td className="text-right num">{s.lot_step}</td>
                      <td className="text-right num">{s.spread?.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Row({ k, v }: { k: string; v: any }) {
  return (
    <div className="flex justify-between items-center">
      <span className="text-muted-foreground">{k}</span>
      <span className="font-medium num">{v ?? "—"}</span>
    </div>
  );
}
