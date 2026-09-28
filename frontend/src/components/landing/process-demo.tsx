"use client";

import { useEffect, useState } from "react";
import { Check, Globe } from "lucide-react";

import { demoScript } from "@/lib/landing/content";
import { cn } from "@/lib/cn";

type Phase = 0 | 1 | 2 | 3;

const PHASE_MS = [1600, 1200, 1200, 3400] as const;

export function ProcessDemo({ className }: { className?: string }) {
  const [animatedPhase, setAnimatedPhase] = useState<Phase>(0);
  const [reduceMotion, setReduceMotion] = useState(false);
  const phase: Phase = reduceMotion ? 3 : animatedPhase;

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setReduceMotion(media.matches);
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);

  useEffect(() => {
    if (reduceMotion) return undefined;
    const timer = window.setTimeout(() => {
      setAnimatedPhase((current) => ((current + 1) % 4) as Phase);
    }, PHASE_MS[animatedPhase]);
    return () => window.clearTimeout(timer);
  }, [animatedPhase, reduceMotion]);

  const visibleSteps = Math.min(phase, 3);
  const done = phase >= 3;

  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-[1.35rem] border border-white/12 bg-[linear-gradient(160deg,rgba(255,255,255,0.09),rgba(255,255,255,0.02))] shadow-[0_40px_90px_-40px_rgba(10,20,60,0.9)] backdrop-blur-xl",
        className
      )}
      aria-label="Демонстрация: от описания задачи до запуска"
    >
      <div
        className="pointer-events-none absolute inset-x-0 top-0 h-px bg-[linear-gradient(90deg,transparent,rgba(140,190,255,0.7),transparent)]"
        aria-hidden
      />

      <div className="flex items-center gap-2.5 border-b border-white/8 px-4 py-3.5 sm:px-5 2xl:px-6 2xl:py-4">
        <span className="flex gap-1.5" aria-hidden>
          <span className="h-2.5 w-2.5 rounded-full bg-white/15" />
          <span className="h-2.5 w-2.5 rounded-full bg-white/15" />
          <span className="h-2.5 w-2.5 rounded-full bg-white/15" />
        </span>
        <p className="ml-1 text-[0.8rem] font-medium text-white/55 2xl:text-[0.88rem]">Чат проекта</p>
      </div>

      <div className="space-y-4 p-4 sm:p-5 2xl:space-y-5 2xl:p-7">
        <div className="ml-auto max-w-[88%] rounded-[1rem] rounded-br-sm bg-[linear-gradient(120deg,#2388ff,#6d6cff)] px-4 py-2.5 text-sm leading-relaxed text-white shadow-[0_12px_30px_-14px_rgba(45,130,255,0.9)] 2xl:px-5 2xl:py-3 2xl:text-[0.98rem]">
          {demoScript.userMessage}
        </div>

        <ul className="space-y-2.5 2xl:space-y-3.5" aria-live="polite">
          {demoScript.steps.map((step, index) => {
            const stepDone = visibleSteps > index;
            const active = visibleSteps === index && phase < 3;
            return (
              <li
                key={step}
                className={cn(
                  "flex items-center gap-3 text-sm transition-opacity duration-500 2xl:gap-3.5 2xl:text-[0.98rem]",
                  stepDone || active ? "opacity-100" : "opacity-25"
                )}
              >
                <span
                  className={cn(
                    "flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold transition-colors duration-300 2xl:h-7 2xl:w-7 2xl:text-xs",
                    stepDone
                      ? "bg-[#5ce6b0]/18 text-[#5ce6b0] ring-1 ring-[#5ce6b0]/40"
                      : active
                        ? "bg-[#4f9dff]/20 text-[#8ec4ff] ring-1 ring-[#4f9dff]/50"
                        : "bg-white/[0.05] text-white/30 ring-1 ring-white/10"
                  )}
                  aria-hidden
                >
                  {stepDone ? <Check size={13} strokeWidth={3} /> : index + 1}
                </span>
                <span className={cn(stepDone ? "text-white/90" : "text-white/55")}>{step}</span>
                {active ? (
                  <span className="ai-typing ml-auto" aria-hidden>
                    <span />
                    <span />
                    <span />
                  </span>
                ) : null}
              </li>
            );
          })}
        </ul>

        <div
          className={cn(
            "flex items-center gap-3 rounded-[0.9rem] border px-4 py-3 transition-all duration-500 2xl:px-5 2xl:py-4",
            done
              ? "translate-y-0 border-[#5ce6b0]/30 bg-[#5ce6b0]/[0.08] opacity-100"
              : "translate-y-1 border-transparent bg-transparent opacity-0"
          )}
        >
          <span
            className="live-dot flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#5ce6b0]/15 text-[#5ce6b0]"
            aria-hidden
          >
            <Globe size={15} />
          </span>
          <div className="min-w-0">
            <p className="text-[0.72rem] font-semibold uppercase tracking-[0.16em] text-[#5ce6b0]">
              Live
            </p>
            <p className="truncate font-mono text-[0.82rem] text-white/85 2xl:text-[0.92rem]">
              {demoScript.url}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
