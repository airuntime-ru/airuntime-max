import Link from "next/link";
import { ArrowRight } from "lucide-react";

import { ProcessDemo } from "@/components/landing/process-demo";
import { Reveal } from "@/components/ui/reveal";
import { LOGIN_HREF, PROCESS_ANCHOR, heroCopy, heroStats } from "@/lib/landing/content";

export function HeroSection() {
  // The hero owns the first screen. Without the min-height, a 1440px-tall display cut
  // the hero off two thirds up and filled the rest of the fold with a band of the white
  // section below it. min-height only ever adds space, so shorter screens are untouched.
  return (
    <section
      className="cosmos cosmos-stars relative flex flex-col justify-center overflow-hidden pt-24 sm:pt-28 lg:min-h-[calc(100svh-4rem)] lg:pt-32 2xl:pt-40"
      aria-labelledby="hero-title"
    >
      {/* Nebula bloom + orbits. All decorative, all transform-only animation. */}
      <div
        className="aurora-blob left-[-14%] top-[-12%] h-[34rem] w-[34rem] bg-[radial-gradient(circle,rgba(35,136,255,0.55),transparent_66%)] 2xl:h-[44rem] 2xl:w-[44rem]"
        aria-hidden
      />
      <div
        className="aurora-blob aurora-blob-slow right-[-16%] top-[6%] h-[30rem] w-[30rem] bg-[radial-gradient(circle,rgba(124,108,255,0.5),transparent_66%)] 2xl:h-[40rem] 2xl:w-[40rem]"
        aria-hidden
      />
      <div
        className="orbit-ring left-1/2 top-[-30%] h-[52rem] w-[52rem] -translate-x-1/2 max-lg:hidden 2xl:h-[66rem] 2xl:w-[66rem]"
        aria-hidden
      />
      <div
        className="orbit-ring orbit-ring-reverse left-1/2 top-[-14%] h-[38rem] w-[38rem] -translate-x-1/2 max-lg:hidden 2xl:h-[50rem] 2xl:w-[50rem]"
        aria-hidden
      />
      <div className="cosmos-horizon" aria-hidden />

      <div className="landing-container relative grid gap-10 pb-16 sm:pb-20 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)] lg:items-center lg:gap-14 lg:pb-24 2xl:gap-20 2xl:pb-32">
        <div className="max-w-[38rem] 2xl:max-w-[44rem] 3xl:max-w-[48rem]">
          <Reveal>
            <p className="inline-flex items-center gap-2 rounded-full border border-white/12 bg-white/[0.06] px-3.5 py-1.5 text-[0.78rem] font-medium tracking-[0.02em] text-white/70 backdrop-blur-sm 2xl:px-4 2xl:py-2 2xl:text-[0.86rem]">
              <span
                className="h-1.5 w-1.5 rounded-full bg-[#5ce6b0] shadow-[0_0_10px_#5ce6b0]"
                aria-hidden
              />
              {heroCopy.eyebrow}
            </p>
          </Reveal>

          <Reveal delay={70}>
            <h1
              id="hero-title"
              className="mt-6 text-[2.6rem] font-semibold leading-[1.16] tracking-[-0.04em] text-white sm:text-[3.4rem] lg:text-[4.35rem] lg:leading-[1.14] 2xl:text-[5rem] 3xl:text-[5.4rem]"
            >
              {heroCopy.titleLine1}
              <br />
              <span className="marker-glow">
                <span>{heroCopy.titleHighlight}</span>
              </span>
            </h1>
          </Reveal>

          <Reveal delay={140}>
            <p className="mt-6 max-w-[33rem] text-lg leading-relaxed text-white/60 sm:text-xl 2xl:mt-7 2xl:max-w-[38rem] 2xl:text-[1.35rem]">
              {heroCopy.subtitle}
            </p>
          </Reveal>

          <Reveal delay={210}>
            <div className="mt-9 flex flex-col gap-3 sm:flex-row sm:items-center 2xl:mt-11 2xl:gap-4">
              <Link
                href={LOGIN_HREF}
                className="btn-glow inline-flex h-13 items-center justify-center gap-2 rounded-full px-7 text-[0.95rem] font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/50 2xl:h-14 2xl:px-8 2xl:text-base"
              >
                {heroCopy.primaryCta}
                <ArrowRight size={17} aria-hidden />
              </Link>
              <a
                href={PROCESS_ANCHOR}
                className="inline-flex h-13 items-center justify-center rounded-full border border-white/15 bg-white/[0.04] px-7 text-[0.95rem] font-semibold text-white/85 transition-colors hover:border-white/30 hover:bg-white/[0.09] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40 2xl:h-14 2xl:px-8 2xl:text-base"
              >
                {heroCopy.secondaryCta}
              </a>
            </div>
          </Reveal>

          <Reveal delay={280}>
            <dl className="mt-12 grid max-w-lg grid-cols-3 gap-4 border-t border-white/10 pt-7 2xl:mt-14 2xl:max-w-xl 2xl:pt-9">
              {heroStats.map((stat) => (
                <div key={stat.label}>
                  <dt className="sr-only">{stat.label}</dt>
                  <dd>
                    <span className="block text-2xl font-semibold tracking-[-0.02em] text-white sm:text-[1.75rem] 2xl:text-[2.15rem]">
                      {stat.value}
                    </span>
                    <span className="mt-1 block text-[0.8rem] leading-snug text-white/55 2xl:text-[0.9rem]">
                      {stat.label}
                    </span>
                  </dd>
                </div>
              ))}
            </dl>
          </Reveal>
        </div>

        <Reveal delay={160} className="lg:pl-4 2xl:pl-8">
          <ProcessDemo />
        </Reveal>
      </div>
    </section>
  );
}
