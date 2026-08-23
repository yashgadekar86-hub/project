"use client";

import { useEffect, useRef, useState } from "react";

type Options = { onMessage?: (msg: any) => void; enabled?: boolean; };
export function useWS(topic: string, opts: Options = {}) {
  const [status, setStatus] = useState<"disconnected" | "connecting" | "connected">("disconnected");
  const [lastMessage, setLastMessage] = useState<any>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (opts.enabled === false) return;
    const token = typeof window !== "undefined" ? localStorage.getItem("aifx_token") : null;
    const url = `${(process.env.NEXT_PUBLIC_WS_URL || (typeof window !== "undefined" ? `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ws` : "ws://localhost:8000/ws"))}${token ? `?token=${token}` : ""}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;
    setStatus("connecting");
    ws.onopen = () => {
      setStatus("connected");
      ws.send(JSON.stringify({ action: "subscribe", topic }));
    };
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.topic === topic || topic === "*") {
          setLastMessage(msg);
          opts.onMessage?.(msg);
        }
      } catch {}
    };
    ws.onclose = () => setStatus("disconnected");
    ws.onerror = () => setStatus("disconnected");
    return () => { ws.close(); wsRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topic, opts.enabled]);

  return { status, lastMessage };
}
