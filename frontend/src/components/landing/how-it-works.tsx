import { MessageSquare, Rocket, Wand2 } from "lucide-react";

import { LandingSection, SectionHeading } from "@/components/landing/section";
import { Reveal } from "@/components/ui/reveal";
import { howCopy, howSteps } from "@/lib/landing/content";

const stepIcons = [MessageSquare, Wand2, Rocket] as const;

export function HowItWorksSection() {
  return (
    <LandingSection id="how-it-works" ariaLabelledBy="how-title">
      <SectionHeading
        eyebrow="Процесс"
        title={howCopy.title}
        description={howCopy.subtitle}
        id="how-title"
        align="center"
      />

      <div className="relative mt-16 2xl:mt-20">
        {/* Rail connecting the three orbs on desktop. */}
        <div
          className="pointer-events-none absolute left-[16%] right-[16%] top-8 hidden h-px bg-[linear-gradient(90deg,transparent,rgba(35,136,255,0.35),rgba(124,108,255,0.35),transparent)] sm:block"
          aria-hidden
        />

        <ol className="relative grid gap-12 sm:grid-cols-3 sm:gap-8 2xl:gap-12 3xl:mx-auto 3xl:max-w-[92rem]">
          {howSteps.map((step, index) => {
            const Icon = stepIcons[index] ?? MessageSquare;
            return (
              <Reveal as="li" key={step.id} delay={index * 110} className="sm:text-center">
                <div className="flex items-center gap-4 sm:flex-col sm:gap-5">
                  <span
                    className="relative flex h-16 w-16 shrink-0 items-center justify-center rounded-full border border-black/[0.06] bg-white text-[var(--ar-sky)] shadow-[0_16px_34px_-18px_rgba(35,136,255,0.75)] sm:mx-auto 2xl:h-[4.5rem] 2xl:w-[4.5rem]"
                    aria-hidden
                  >
                    <Icon size={22} />
                    <span className="absolute -right-1 -top-1 flex h-6 w-6 items-center justify-center rounded-full bg-[image:var(--ar-accent-gradient)] text-[0.7rem] font-bold text-white">
                      {index + 1}
                    </span>
                  </span>
                  <h3 className="text-xl font-semibold tracking-[-0.02em] text-[var(--ar-black)] sm:text-[1.35rem] 2xl:text-[1.6rem]">
                    {step.title}
                  </h3>
                </div>
                <p className="mt-3 text-base leading-relaxed text-[var(--ar-mist)] sm:mx-auto sm:max-w-[19rem] 2xl:mt-4 2xl:max-w-[23rem] 2xl:text-[1.05rem] 3xl:max-w-[25rem]">
                  {step.detail}
                </p>
              </Reveal>
            );
          })}
        </ol>
      </div>
    </LandingSection>
  );
}
