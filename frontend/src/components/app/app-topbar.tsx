"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogOut, Zap } from "lucide-react";

import { Logo } from "@/components/brand/logo";
import { cn } from "@/lib/cn";

const nav = [
  {
    href: "/app",
    label: "Проекты",
    match: (path: string) => path === "/app" || path.startsWith("/app/projects"),
  },
  {
    href: "/app/profile",
    label: "Профиль",
    match: (path: string) => path === "/app/profile",
  },
];

/**
 * Floating chrome for the whole cabinet, replacing the old left sidebar: the workspace gets
 * its full width back and the cabinet reads as the same product as the landing rather than a
 * generic admin panel. Below lg the nav lives in the bottom bar instead (see AppMobileNav).
 */
export function AppTopBar({ credits, onLogout }: { credits: number; onLogout: () => void }) {
  const pathname = usePathname();

  return (
    <header className="fixed inset-x-0 top-0 z-[60] px-3 pt-3 sm:px-4 lg:px-6">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 rounded-full border border-black/[0.06] bg-white/80 px-3 shadow-[0_10px_34px_-18px_rgba(15,23,42,0.5)] backdrop-blur-xl sm:px-4">
        <Logo href="/app" variant="full" theme="dark" size="sm" />

        <nav className="ml-4 hidden items-center gap-1 lg:flex" aria-label="Разделы кабинета">
          {nav.map((item) => {
            const active = item.match(pathname);
            return (
              <Link
                key={item.href}
                href={item.href}
                data-tour={item.href === "/app" ? "nav-projects" : undefined}
                className={cn(
                  "rounded-full px-4 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ar-sky)]/35",
                  active
                    ? "bg-[var(--ar-black)] text-white"
                    : "text-[var(--ar-mist)] hover:bg-black/[0.04] hover:text-[var(--ar-black)]"
                )}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <Link
            href="/app/profile"
            title="Баланс кредитов"
            className="inline-flex items-center gap-1.5 rounded-full border border-black/[0.06] bg-[image:var(--ar-accent-gradient-soft)] px-3 py-1.5 text-sm transition-colors hover:border-[var(--ar-sky)]/35 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ar-sky)]/35"
          >
            <Zap size={13} className="text-[var(--ar-sky)]" aria-hidden />
            <span className="font-semibold tabular-nums text-[var(--ar-black)]">
              {credits.toLocaleString("ru-RU")}
            </span>
            <span className="sr-only">кредитов</span>
          </Link>

          <button
            type="button"
            onClick={onLogout}
            aria-label="Выйти"
            title="Выйти"
            className="hidden h-10 w-10 items-center justify-center rounded-full text-[var(--ar-stone)] transition-colors hover:bg-black/[0.05] hover:text-[var(--ar-black)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ar-sky)]/35 lg:inline-flex"
          >
            <LogOut size={17} aria-hidden />
          </button>
        </div>
      </div>
    </header>
  );
}
