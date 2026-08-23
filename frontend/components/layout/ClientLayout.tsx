"use client";

import { useEffect, useRef } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth, useAuthSetup, DEV_BYPASS } from "@/hooks/useAuth";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { Loader2 } from "lucide-react";

// Paths that render without the app shell (login/register).
const AUTH_PATHS = ["/login", "/register"];

export function ClientLayout({ children }: { children: React.ReactNode }) {
  useAuthSetup();

  const user = useAuth((s) => s.user);
  const loading = useAuth((s) => s.loading);
  const pathname = usePathname();
  const router = useRouter();
  const lastNav = useRef<number>(0);

  const isAuthPage = AUTH_PATHS.some((p) => pathname?.startsWith(p));

  // In dev-bypass mode, bounce off /login and /register immediately
  // (client-side, no waiting for hydrate) since they are disabled.
  useEffect(() => {
    if (DEV_BYPASS && isAuthPage) {
      const now = Date.now();
      if (now - lastNav.current < 500) return;
      lastNav.current = now;
      window.location.replace("/dashboard");
    }
  }, [isAuthPage]);

  // Normal (non-dev) route guard.
  useEffect(() => {
    if (DEV_BYPASS) return;
    if (loading) return;
    if (!user && !isAuthPage) {
      const now = Date.now();
      if (now - lastNav.current < 1500) return;
      lastNav.current = now;
      const hadToken = typeof window !== "undefined" && !!window.localStorage.getItem("aifx_token");
      router.replace(hadToken ? "/login?reason=expired" : "/login");
    } else if (user && isAuthPage) {
      const now = Date.now();
      if (now - lastNav.current < 1500) return;
      lastNav.current = now;
      router.replace("/dashboard");
    }
  }, [user, loading, pathname, router, isAuthPage]);

  // Dev-bypass: render the app shell immediately. The Zustand store is
  // pre-seeded synchronously with the demo user, so no loader needed.
  if (DEV_BYPASS) {
    if (isAuthPage) {
      return (
        <div className="h-screen flex items-center justify-center bg-background">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
        </div>
      );
    }
    return (
      <div className="h-screen flex bg-background">
        <Sidebar />
        <div className="flex-1 flex flex-col min-w-0">
          <TopBar />
          <main className="flex-1 overflow-y-auto p-4 md:p-6">{children}</main>
        </div>
      </div>
    );
  }

  if (loading || (!user && !isAuthPage)) {
    return (
      <div className="h-screen flex items-center justify-center bg-background">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  if (isAuthPage) return <>{children}</>;

  return (
    <div className="h-screen flex bg-background">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <TopBar />
        <main className="flex-1 overflow-y-auto p-4 md:p-6">{children}</main>
      </div>
    </div>
  );
}
