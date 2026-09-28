"use client";

import { Accordion } from "@/components/ui/accordion";
import { faqs } from "@/lib/landing/content";

export function FaqAccordion() {
  return <Accordion items={faqs} />;
}
