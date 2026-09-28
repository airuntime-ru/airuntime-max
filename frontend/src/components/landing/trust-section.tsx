import { Boxes, Database, Globe2, History, Rocket, ShieldCheck } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { LandingSection, SectionHeading } from "@/components/landing/section";
import { Reveal } from "@/components/ui/reveal";
import { trustCopy, trustPoints } from "@/lib/landing/content";

const icons: LucideIcon[] = [Boxes, Rocket, Database, ShieldCheck, History, Globe2];

export function TrustSection() {
  return (
    <LandingSection id="trust" ariaLabelledBy="trust-title" tone="cosmos">
      <div
        className="aurora-blob left-[-10%] top-[10%] h-[26rem] w-[26rem] bg-[radial-gradient(circle,rgba(35,136,255,0.4),transparent_66%)] 2xl:h-[34rem] 2xl:w-[34rem]"
        aria-hidden
      />
      <div
        className="aurora-blob aurora-blob-slow right-[-12%] bottom-[6%] h-[24rem] w-[24rem] bg-[radial-gradient(circle,rgba(92,230,176,0.28),transparent_66%)] 2xl:h-[32rem] 2xl:w-[32rem]"
        aria-hidden
      />

      <SectionHeading
        eyebrow="Платформа"
        title={trustCopy.title}
        description={trustCopy.subtitle}
        id="trust-title"
        tone="dark"
        align="center"
      />

      <ul className="mt-14 grid gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:mt-16 2xl:gap-5">
        {trustPoints.map((point, index) => {
          const Icon = icons[index] ?? Boxes;
          return (
            <Reveal as="li" key={point.title} delay={index * 80}>
              <div className="void-card void-card-hover h-full rounded-[1.1rem] p-6 2xl:p-7">
                <span
                  className="flex h-11 w-11 items-center justify-center rounded-[0.8rem] border border-white/10 bg-white/[0.06] text-[#8ec4ff]"
                  aria-hidden
                >
                  <Icon size={19} />
                </span>
                <h3 className="mt-5 text-base font-semibold text-white 2xl:text-lg">{point.title}</h3>
                <p className="mt-2 text-[0.92rem] leading-relaxed text-white/60 2xl:text-[1rem]">
                  {point.detail}
                </p>
              </div>
            </Reveal>
          );
        })}
      </ul>

      <Reveal delay={140}>
        <p className="mx-auto mt-12 max-w-2xl text-center text-[0.95rem] leading-relaxed text-white/55 2xl:mt-14 2xl:max-w-3xl 2xl:text-[1.05rem]">
          {trustCopy.audienceLine}
        </p>
      </Reveal>
    </LandingSection>
  );
}
