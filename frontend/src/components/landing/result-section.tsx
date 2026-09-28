import { Bot, Lock } from "lucide-react";

import { LandingSection, SectionHeading } from "@/components/landing/section";
import { Reveal } from "@/components/ui/reveal";
import { resultCopy } from "@/lib/landing/content";

const trustStrip = ["Docker build", "HTTPS", "Версии в Git", "Runtime live"] as const;

const services = [
  { name: "Диагностика", price: "1 500 ₽" },
  { name: "Замена масла", price: "900 ₽" },
  { name: "Шиномонтаж", price: "2 400 ₽" },
] as const;

export function ResultSection() {
  return (
    <LandingSection id="capabilities" ariaLabelledBy="result-title" tone="muted">
      <SectionHeading
        eyebrow="Результат"
        title={resultCopy.title}
        description={resultCopy.subtitle}
        id="result-title"
        align="center"
      />

      <div className="mt-14 grid gap-6 lg:grid-cols-2 2xl:mx-auto 2xl:mt-16 2xl:max-w-[78rem] 2xl:gap-8 3xl:max-w-[92rem]">
        {/* Browser window */}
        <Reveal>
          <article className="sky-card sky-card-hover h-full rounded-[1.25rem]">
            <div className="flex items-center gap-2.5 border-b border-black/[0.06] bg-[#fbfcfe] px-4 py-3">
              <span className="flex gap-1.5" aria-hidden>
                <span className="h-2.5 w-2.5 rounded-full bg-[#ff5f57]" />
                <span className="h-2.5 w-2.5 rounded-full bg-[#febc2e]" />
                <span className="h-2.5 w-2.5 rounded-full bg-[#28c840]" />
              </span>
              <p className="ml-2 inline-flex min-w-0 items-center gap-1.5 rounded-full bg-black/[0.045] px-3 py-1">
                <Lock size={11} className="shrink-0 text-emerald-600" aria-hidden />
                <span className="truncate font-mono text-[0.72rem] text-[var(--ar-mist)]">
                  autoservice.airuntime.ru
                </span>
              </p>
            </div>

            <div className="p-5 sm:p-7">
              <p className="text-[0.7rem] font-semibold uppercase tracking-[0.18em] text-[var(--ar-sky)]">
                Автосервис на Лесной
              </p>
              <p className="mt-2.5 text-[1.6rem] font-semibold leading-[1.15] tracking-[-0.03em] text-[var(--ar-black)] sm:text-3xl 2xl:text-[2.1rem]">
                Запишитесь <span className="text-daylight">за минуту</span>
              </p>

              <ul className="mt-6 space-y-2">
                {services.map((service) => (
                  <li
                    key={service.name}
                    className="flex items-center justify-between rounded-[0.7rem] border border-black/[0.06] bg-white px-3.5 py-2.5"
                  >
                    <span className="text-sm text-[var(--ar-graphite)] 2xl:text-[0.95rem]">
                      {service.name}
                    </span>
                    <span className="text-sm font-semibold tabular-nums text-[var(--ar-black)] 2xl:text-[0.95rem]">
                      {service.price}
                    </span>
                  </li>
                ))}
              </ul>

              <p className="btn-glow mt-5 flex h-11 items-center justify-center rounded-[0.7rem] text-sm font-semibold 2xl:h-12 2xl:text-[0.95rem]">
                Записаться
              </p>
            </div>
          </article>
        </Reveal>

        {/* Telegram chat */}
        <Reveal delay={110}>
          <article className="sky-card sky-card-hover flex h-full flex-col rounded-[1.25rem]">
            <div className="flex items-center gap-3 border-b border-black/[0.06] bg-[#fbfcfe] px-4 py-3">
              <span
                className="flex h-9 w-9 items-center justify-center rounded-full bg-[image:var(--ar-accent-gradient)] text-white"
                aria-hidden
              >
                <Bot size={17} />
              </span>
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-[var(--ar-black)]">
                  @autoservice_bot
                </p>
                <p className="flex items-center gap-1.5 text-[0.72rem] text-emerald-600">
                  <span
                    className="live-dot h-1.5 w-1.5 rounded-full bg-emerald-500 text-emerald-500"
                    aria-hidden
                  />
                  онлайн
                </p>
              </div>
            </div>

            <div className="flex flex-1 flex-col gap-2.5 bg-[linear-gradient(180deg,#f6f9ff,#eef4fd)] p-5 sm:p-7">
              <p className="w-fit max-w-[88%] rounded-[1rem] rounded-bl-sm bg-white px-3.5 py-2.5 text-sm leading-relaxed text-[var(--ar-black)] shadow-[0_2px_8px_rgba(15,23,42,0.06)] 2xl:text-[0.95rem]">
                Запишите меня на завтра, 11:00
              </p>
              <p className="ml-auto w-fit max-w-[88%] rounded-[1rem] rounded-br-sm bg-[linear-gradient(120deg,#2388ff,#6d6cff)] px-3.5 py-2.5 text-sm leading-relaxed text-white shadow-[0_10px_24px_-14px_rgba(45,130,255,0.9)] 2xl:text-[0.95rem]">
                Готово ✅ Завтра в 11:00, замена масла. Напомню за час.
              </p>
              <p className="w-fit max-w-[88%] rounded-[1rem] rounded-bl-sm bg-white px-3.5 py-2.5 text-sm leading-relaxed text-[var(--ar-black)] shadow-[0_2px_8px_rgba(15,23,42,0.06)] 2xl:text-[0.95rem]">
                А цену подскажешь?
              </p>
              <p className="ml-auto inline-flex w-fit items-center rounded-[1rem] rounded-br-sm bg-[linear-gradient(120deg,#2388ff,#6d6cff)] px-4 py-3">
                <span className="ai-typing" aria-label="бот печатает">
                  <span />
                  <span />
                  <span />
                </span>
              </p>
            </div>
          </article>
        </Reveal>
      </div>

      <Reveal delay={180}>
        <ul className="mt-10 flex flex-wrap justify-center gap-2.5 2xl:mt-12 2xl:gap-3">
          {trustStrip.map((item) => (
            <li
              key={item}
              className="inline-flex items-center gap-2 rounded-full border border-black/[0.07] bg-white px-4 py-2 text-[0.82rem] font-medium text-[var(--ar-graphite)] 2xl:px-5 2xl:py-2.5 2xl:text-[0.9rem]"
            >
              <span className="text-emerald-500" aria-hidden>
                ✓
              </span>
              {item}
            </li>
          ))}
        </ul>
      </Reveal>
    </LandingSection>
  );
}
