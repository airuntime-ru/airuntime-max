"use client";

import type { ReactNode } from "react";
import Link from "next/link";

import { Logo } from "@/components/brand/logo";

const highlights = [
  "Docker-сборка и автодеплой",
  "Секреты не уходят в модель",
  "Версии и rollback",
] as const;

/**
 * Airlock between the landing and the cabinet: the deep-space backdrop of the landing,
 * with the form itself on a plain white card so the auth pages stay light-themed.
 */
export function AuthShell({ children }: { children: ReactNode }) {
  return (
    <div className="cosmos cosmos-stars relative min-h-screen overflow-hidden">
      <div
        className="aurora-blob left-[-12%] top-[-10%] h-[30rem] w-[30rem] bg-[radial-gradient(circle,rgba(35,136,255,0.5),transparent_66%)]"
        aria-hidden
      />
      <div
        className="aurora-blob aurora-blob-slow bottom-[-14%] right-[-10%] h-[26rem] w-[26rem] bg-[radial-gradient(circle,rgba(124,108,255,0.42),transparent_66%)]"
        aria-hidden
      />

      <div className="relative z-10 mx-auto grid min-h-screen max-w-5xl items-center gap-10 px-5 py-12 sm:px-6 lg:grid-cols-[0.95fr_1fr] lg:px-8">
        <div className="hidden lg:block">
          <Logo href="/" variant="full" theme="light" size="md" />
          <h1 className="mt-10 max-w-md text-[2.6rem] font-semibold leading-[1.1] tracking-[-0.035em] text-white">
            Продолжайте <span className="text-cosmic">собирать</span> проекты
          </h1>
          <p className="mt-5 max-w-md text-base leading-relaxed text-white/55">
            Одноразовый код по почте, без лишних паролей.
          </p>
          <ul className="mt-9 space-y-3">
            {highlights.map((item) => (
              <li key={item} className="flex items-center gap-3 text-sm text-white/60">
                <span
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-white/12 bg-white/[0.06] text-[0.7rem] text-[#5ce6b0]"
                  aria-hidden
                >
                  ✓
                </span>
                {item}
              </li>
            ))}
          </ul>
        </div>

        <div className="mx-auto w-full max-w-md">
          <div className="mb-8 flex justify-center lg:hidden">
            <Logo href="/" variant="full" theme="light" size="md" />
          </div>
          <div className="rounded-[1.25rem] border border-white/60 bg-white p-6 shadow-[0_40px_90px_-40px_rgba(3,8,24,0.95)] sm:p-8">
            {children}
          </div>
          <p className="mt-5 text-center text-xs text-white/55">
            Вернуться на{" "}
            <Link href="/" className="font-medium text-[#8ec4ff] hover:underline">
              лендинг AIRuntime
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
