import "./globals.css";
import type { Metadata } from "next";
import { ClientLayout } from "@/components/layout/ClientLayout";

export const metadata: Metadata = {
  title: "AI Forex Command Center",
  description: "Production-grade AI-powered Forex trading platform for MetaTrader 5.",
};

// Note: we deliberately do NOT use next/font/google here (no network in sandbox) and
// we do NOT emit an inline <style> tag with font-family strings — that caused a
// hydration mismatch because the SSR serialized &quot; entities and the client re-parsed
// them as " characters, producing a text-content mismatch on :root.
// Font stacks are declared in app/globals.css under :root { --font-sans / --font-mono }.

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body className="font-sans antialiased">
        <ClientLayout>{children}</ClientLayout>
      </body>
    </html>
  );
}
