import type { Metadata } from "next";

import { FaqSection } from "@/components/landing/faq-section";
import { FinalCtaSection } from "@/components/landing/final-cta-section";
import { HeroSection } from "@/components/landing/hero-section";
import { HowItWorksSection } from "@/components/landing/how-it-works";
import { MarqueeStrip } from "@/components/landing/marquee-strip";
import { PricingSection } from "@/components/landing/pricing-section";
import { ResultSection } from "@/components/landing/result-section";
import { SiteFooter } from "@/components/landing/site-footer";
import { SiteHeader } from "@/components/landing/site-header";
import { TrustSection } from "@/components/landing/trust-section";
import { UseCasesSection } from "@/components/landing/use-cases-section";
import { seoCopy } from "@/lib/landing/content";

export const metadata: Metadata = {
  title: {
    absolute: seoCopy.title,
  },
  description: seoCopy.description,
  openGraph: {
    title: seoCopy.title,
    description: seoCopy.description,
  },
  twitter: {
    title: seoCopy.title,
    description: seoCopy.description,
  },
};

const jsonLd = {
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  name: "AIRuntime",
  applicationCategory: "DeveloperApplication",
  operatingSystem: "Web",
  description: seoCopy.description,
  url: "https://airuntime.ru",
};

export default function Home() {
  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />
      {/* .reveal starts at opacity 0 and is un-hidden by an IntersectionObserver, so without
          JS the whole page would render blank. */}
      <noscript>
        <style>{".reveal{opacity:1!important;transform:none!important}"}</style>
      </noscript>
      <div className="landing-shell min-h-screen bg-white text-[var(--ar-black)]">
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-white focus:px-4 focus:py-2 focus:shadow-lg"
        >
          Перейти к содержимому
        </a>
        <SiteHeader />
        <main id="main-content">
          <HeroSection />
          <MarqueeStrip />
          <ResultSection />
          <HowItWorksSection />
          <UseCasesSection />
          <TrustSection />
          <PricingSection />
          <FaqSection />
          <FinalCtaSection />
        </main>
        <SiteFooter />
      </div>
    </>
  );
}
