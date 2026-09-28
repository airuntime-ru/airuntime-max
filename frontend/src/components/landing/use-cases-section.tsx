import { Building2, CalendarCheck, Megaphone, Send } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { LandingSection, SectionHeading } from "@/components/landing/section";
import { Reveal } from "@/components/ui/reveal";
import { useCases, useCasesCopy } from "@/lib/landing/content";

const icons: Record<string, LucideIcon> = {
  company: Building2,
  landing: Megaphone,
  booking: CalendarCheck,
  telegram: Send,
};

export function UseCasesSection() {
  return (
    <LandingSection id="use-cases" ariaLabelledBy="use-cases-title" tone="muted">
      <SectionHeading
        eyebrow="Сценарии"
        title={useCasesCopy.title}
        description={useCasesCopy.subtitle}
        id="use-cases-title"
        align="center"
      />

      <ul className="mt-14 grid gap-5 sm:grid-cols-2 2xl:mt-16 2xl:grid-cols-4">
        {useCases.map((item, index) => {
          const Icon = icons[item.id] ?? Building2;
          return (
            <Reveal as="li" key={item.id} delay={index * 90}>
              <article className="sky-card sky-card-hover h-full rounded-[1.1rem] p-6 sm:p-7">
                <span
                  className="flex h-11 w-11 items-center justify-center rounded-[0.8rem] bg-[image:var(--ar-accent-gradient-soft)] text-[var(--ar-sky)]"
                  aria-hidden
                >
                  <Icon size={19} />
                </span>
                <h3 className="mt-5 text-lg font-semibold tracking-[-0.02em] text-[var(--ar-black)] 2xl:text-xl">
                  {item.title}
                </h3>
                <p className="mt-2 text-[0.95rem] leading-relaxed text-[var(--ar-mist)]">
                  {item.detail}
                </p>
                <p className="mt-5 rounded-[0.75rem] rounded-bl-sm border border-black/[0.06] bg-[#f6f9fe] px-3.5 py-2.5 text-sm text-[var(--ar-graphite)]">
                  {item.prompt}
                </p>
              </article>
            </Reveal>
          );
        })}
      </ul>
    </LandingSection>
  );
}
