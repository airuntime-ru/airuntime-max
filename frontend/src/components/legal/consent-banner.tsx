"use client";

import { useSyncExternalStore } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Button } from "@/components/ui/button";
import {
  getAnalyticsConsent,
  setAnalyticsConsent,
  subscribeAnalyticsConsent,
  type AnalyticsConsent,
} from "@/lib/consent";

function readConsent(): AnalyticsConsent | null | "pending" {
  return getAnalyticsConsent();
}

function pendingConsent(): AnalyticsConsent | null | "pending" {
  return "pending";
}

/**
 * Asks for consent before product analytics / similar non-essential storage.
 * Necessary login tokens are set only after auth and are not gated here.
 */
export function ConsentBanner() {
  const choice = useSyncExternalStore(subscribeAnalyticsConsent, readConsent, pendingConsent);
  const pathname = usePathname();

  // The MAX mini app is a messenger surface, not a website: it sets no analytics storage of
  // its own, and a consent sheet over a half-screen webview would cover the booking form.
  if (pathname?.startsWith("/max")) return null;
  if (choice === "pending" || choice !== null) return null;

  return (
    <div
      role="dialog"
      aria-label="Согласие на аналитику"
      className="fixed inset-x-0 bottom-0 z-[80] p-4 sm:p-6"
    >
      <div className="mx-auto flex max-w-3xl flex-col gap-4 rounded-[1.1rem] border border-black/10 bg-white p-4 shadow-[0_24px_60px_-28px_rgba(3,8,24,0.55)] sm:flex-row sm:items-end sm:gap-6 sm:p-5">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-[var(--ar-black)]">Файлы cookie и аналитика</p>
          <p className="mt-1.5 text-sm leading-relaxed text-[var(--ar-mist)]">
            Мы используем необходимые данные для входа после авторизации. Продуктовую аналитику
            (анонимный идентификатор, просмотры экранов) включаем только с вашего согласия. Подробнее
            в{" "}
            <Link href="/legal/cookies" className="text-[var(--ar-sky)] hover:underline">
              политике cookie
            </Link>{" "}
            и{" "}
            <Link href="/legal/privacy" className="text-[var(--ar-sky)] hover:underline">
              политике конфиденциальности
            </Link>
            .
          </p>
        </div>
        <div className="flex shrink-0 flex-col gap-2 sm:flex-row">
          <Button
            variant="outline"
            className="w-full sm:w-auto"
            onClick={() => setAnalyticsConsent("denied")}
          >
            Только необходимые
          </Button>
          <Button
            variant="accent"
            className="w-full sm:w-auto"
            onClick={() => setAnalyticsConsent("granted")}
          >
            Принять
          </Button>
        </div>
      </div>
    </div>
  );
}
