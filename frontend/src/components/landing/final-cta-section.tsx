import Link from "next/link";
import { ArrowRight } from "lucide-react";

import { Reveal } from "@/components/ui/reveal";
import { LOGIN_HREF, finalCtaCopy } from "@/lib/landing/content";

export function FinalCtaSection() {
  return (
    <section
      className="cosmos cosmos-stars relative overflow-hidden py-24 sm:py-28 lg:py-36 2xl:py-44"
      aria-labelledby="final-cta-title"
    >
      <div
        className="aurora-blob left-1/2 top-1/2 h-[36rem] w-[36rem] -translate-x-1/2 -translate-y-1/2 bg-[radial-gradient(circle,rgba(35,136,255,0.45),transparent_64%)] 2xl:h-[46rem] 2xl:w-[46rem]"
        aria-hidden
      />
      <div
        className="orbit-ring left-1/2 top-1/2 h-[44rem] w-[44rem] -translate-x-1/2 -translate-y-1/2 max-sm:hidden 2xl:h-[58rem] 2xl:w-[58rem]"
        aria-hidden
      />

      <div className="relative mx-auto max-w-2xl px-5 text-center sm:px-8 2xl:max-w-3xl 3xl:max-w-4xl">
        <Reveal>
          <h2
            id="final-cta-title"
            className="text-balance text-[2.2rem] font-semibold leading-[1.1] tracking-[-0.035em] text-white sm:text-[3rem] 2xl:text-[3.6rem]"
          >
            {finalCtaCopy.title}
          </h2>
        </Reveal>
        <Reveal delay={80}>
          <p className="mx-auto mt-5 max-w-lg text-base leading-relaxed text-white/55 sm:text-lg 2xl:max-w-xl 2xl:text-xl">
            {finalCtaCopy.subtitle}
          </p>
        </Reveal>
        <Reveal delay={160}>
          <div className="mt-10 flex flex-col items-center gap-5">
            <Link
              href={LOGIN_HREF}
              className="btn-glow inline-flex h-14 items-center justify-center gap-2 rounded-full px-9 text-base font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/50 2xl:h-16 2xl:px-10 2xl:text-lg"
            >
              {finalCtaCopy.primaryCta}
              <ArrowRight size={18} aria-hidden />
            </Link>
            <Link
              href={LOGIN_HREF}
              className="text-sm font-medium text-white/55 underline-offset-4 transition-colors hover:text-white hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/35"
            >
              {finalCtaCopy.secondaryCta}
            </Link>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
