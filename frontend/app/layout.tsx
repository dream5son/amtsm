import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "AMTSM | A股交易信号监控",
  description: "A-share trading signal monitor",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen bg-slate-50 text-slate-900 antialiased">
        <div className="flex min-h-screen flex-col">
          <header className="sticky top-0 z-20 border-b border-slate-200/80 bg-white/95 backdrop-blur">
            <div className="flex w-full items-center justify-between px-4 py-3 md:px-6">
              <Link href="/" className="text-base font-semibold tracking-tight text-slate-900">
                A股交易信号监控工作台
              </Link>
              <nav className="flex items-center gap-1 text-sm text-slate-600">
                <Link href="/" className="rounded-md px-3 py-2 transition-colors hover:bg-slate-100 hover:text-slate-900">
                  Be water, My Friend
                </Link>
              </nav>
            </div>
          </header>

          <main className="flex-1">{children}</main>

          <footer className="border-t border-slate-200 bg-white">
            <div className="mx-auto flex w-full max-w-7xl flex-wrap items-center justify-between gap-2 px-4 py-4 text-xs text-slate-500 md:px-6">
              <span>AMTSM · A股交易信号监控</span>
              <span>Focus · Simplicity · Reliability</span>
            </div>
          </footer>
        </div>
      </body>
    </html>
  );
}
