"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { ArrowLeft, ArrowRight, Check, Sparkles, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

type TourStep = {
  route: string;
  selector: string;
  title: string;
  text: string;
  placement?: "bottom" | "center";
};

const steps: TourStep[] = [
  {
    route: "/app",
    selector: "[data-tour='app-workspace']",
    title: "Добро пожаловать в кабинет",
    text: "Здесь собираются проекты, статусы запусков и быстрые действия. Коротко покажу, где что находится.",
    placement: "center",
  },
  {
    route: "/app",
    selector: "[data-tour='nav-projects']",
    title: "Навигация всегда рядом",
    text: "Сверху на больших экранах и снизу на телефоне — проекты и профиль.",
  },
  {
    route: "/app",
    selector: "[data-tour='project-create-trigger']",
    title: "Опишите проект",
    text: "Нажмите «Новый проект», дайте ему имя — откроется чат, где можно описать задачу.",
  },
  {
    route: "/app",
    selector: "[data-tour='project-list']",
    title: "Возвращайтесь к проектам",
    text: "Все созданные проекты остаются здесь. Откройте проект — сразу попадёте в чат, а деплои и настройки лежат во вкладках внутри.",
  },
];

type HighlightRect = {
  top: number;
  left: number;
  width: number;
  height: number;
};

export function OnboardingTour({
  completed,
  onComplete,
  onLogout,
}: {
  completed: boolean;
  onComplete: () => void | Promise<void>;
  onLogout?: () => void | Promise<void>;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [active, setActive] = useState(false);
  const [index, setIndex] = useState(0);
  const [rect, setRect] = useState<HighlightRect | null>(null);

  const step = steps[index];
  const isLast = index === steps.length - 1;

  useEffect(() => {
    if (completed) return undefined;
    const timer = window.setTimeout(() => setActive(true), 650);
    return () => window.clearTimeout(timer);
  }, [completed]);

  useEffect(() => {
    if (!active || pathname === step.route) return;
    router.push(step.route);
  }, [active, pathname, router, step.route]);

  const updateRect = useCallback(() => {
    if (!active) return;
    // Several tour targets exist twice - once in the top bar (desktop) and once in the bottom
    // bar (mobile), each hidden at the other breakpoint. querySelector would happily return
    // the hidden one and highlight a zero-size box, so take the first one actually rendered.
    const element = Array.from(document.querySelectorAll(step.selector)).find(
      (node) => node.getClientRects().length > 0
    );
    if (!element) {
      setRect(null);
      return;
    }

    element.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
    window.setTimeout(() => {
      const box = element.getBoundingClientRect();
      const margin = 10;
      setRect({
        top: Math.max(8, box.top - margin),
        left: Math.max(8, box.left - margin),
        width: Math.min(window.innerWidth - 16, box.width + margin * 2),
        height: Math.min(window.innerHeight - 16, box.height + margin * 2),
      });
    }, 220);
  }, [active, step.selector]);

  useEffect(() => {
    if (!active) return;
    const timer = window.setTimeout(updateRect, 280);
    window.addEventListener("resize", updateRect);
    window.addEventListener("scroll", updateRect, true);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("resize", updateRect);
      window.removeEventListener("scroll", updateRect, true);
    };
  }, [active, index, pathname, updateRect]);

  const finish = () => {
    setActive(false);
    void onComplete();
  };

  const leaveAccount = () => {
    setActive(false);
    void onLogout?.();
  };

  const goTo = (nextIndex: number) => {
    const next = Math.max(0, Math.min(steps.length - 1, nextIndex));
    setIndex(next);
    const nextRoute = steps[next].route;
    if (pathname !== nextRoute) {
      router.push(nextRoute);
    }
  };

  const progress = useMemo(() => `${index + 1} / ${steps.length}`, [index]);

  if (completed || !active) return null;

  return (
    <div className="fixed inset-0 z-50">
      {/* Blocks the workspace, but not the top bar (z-60) so «Выйти» stays clickable. */}
      <div className="absolute inset-0 bg-slate-950/38 backdrop-blur-[2px]" />

      {rect ? (
        <div
          className="pointer-events-none absolute rounded-[var(--ar-radius-sm)] border-2 border-white shadow-[0_0_0_9999px_rgba(8,20,38,0.42),0_18px_70px_rgba(35,136,255,0.35)] transition-all duration-300"
          style={{
            top: rect.top,
            left: rect.left,
            width: rect.width,
            height: rect.height,
          }}
        />
      ) : null}

      <div
        className={cn(
          "absolute left-1/2 w-[calc(100%-2rem)] max-w-md -translate-x-1/2 rounded-[var(--ar-radius-sm)] border border-[var(--ar-border)] bg-white p-5 shadow-[0_28px_90px_rgba(8,20,38,0.22)]",
          step.placement === "center" ? "top-1/2 -translate-y-1/2" : "bottom-[calc(1rem+env(safe-area-inset-bottom))] lg:bottom-8"
        )}
      >
        <div className="mb-4 flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[var(--ar-radius-sm)] bg-sky-50 text-[var(--ar-sky)]">
              <Sparkles size={18} />
            </span>
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.2em] text-[var(--ar-sky)]">Обучение</p>
              <h2 className="mt-1 text-xl font-semibold text-[var(--ar-black)]">{step.title}</h2>
            </div>
          </div>
          <button
            type="button"
            onClick={finish}
            className="rounded-[var(--ar-radius-sm)] p-2 text-[var(--ar-stone)] hover:bg-sky-50 hover:text-[var(--ar-black)]"
            aria-label="Закрыть онбординг"
          >
            <X size={18} />
          </button>
        </div>

        <p className="text-sm leading-relaxed text-[var(--ar-mist)]">{step.text}</p>

        <div className="mt-5 flex items-center gap-1.5">
          {steps.map((item, itemIndex) => (
            <span
              key={item.title}
              className={cn(
                "h-1.5 flex-1 rounded-full transition-colors",
                itemIndex <= index ? "bg-[var(--ar-sky)]" : "bg-sky-100"
              )}
            />
          ))}
        </div>

        <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <p className="text-xs font-medium text-[var(--ar-stone)]">{progress}</p>
            {onLogout ? (
              <button
                type="button"
                onClick={leaveAccount}
                className="text-xs font-medium text-[var(--ar-mist)] underline-offset-2 hover:text-[var(--ar-black)] hover:underline"
              >
                Выйти
              </button>
            ) : null}
          </div>
          <div className="flex items-center justify-between gap-2 sm:justify-end">
            <Button variant="ghost" size="sm" onClick={finish}>
              Пропустить
            </Button>
            <Button variant="outline" size="sm" onClick={() => goTo(index - 1)} disabled={index === 0}>
              <ArrowLeft size={15} />
              Назад
            </Button>
            <Button variant="accent" size="sm" onClick={() => (isLast ? finish() : goTo(index + 1))}>
              {isLast ? (
                <>
                  Готово
                  <Check size={15} />
                </>
              ) : (
                <>
                  Далее
                  <ArrowRight size={15} />
                </>
              )}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
