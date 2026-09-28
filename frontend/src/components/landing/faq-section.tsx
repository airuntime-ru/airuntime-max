import { LandingSection, SectionHeading } from "@/components/landing/section";
import { FaqAccordion } from "@/components/landing/faq-accordion";
import { Reveal } from "@/components/ui/reveal";

export function FaqSection() {
  return (
    <LandingSection id="faq" ariaLabelledBy="faq-title">
      <SectionHeading eyebrow="FAQ" title="Частые вопросы" id="faq-title" align="center" />
      <Reveal delay={80} className="mx-auto mt-12 max-w-3xl 2xl:mt-14 2xl:max-w-4xl 3xl:max-w-5xl">
        <FaqAccordion />
      </Reveal>
    </LandingSection>
  );
}
