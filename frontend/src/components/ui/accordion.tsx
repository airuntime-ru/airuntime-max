"use client";

import { useId, useState } from "react";
import { ChevronDown } from "lucide-react";
import { motion, AnimatePresence, useReducedMotion } from "framer-motion";

import { cn } from "@/lib/cn";

type Item = { question: string; answer: string };

type AccordionProps = {
  items: Item[];
  onOpenChange?: (index: number | null) => void;
};

export function Accordion({ items, onOpenChange }: AccordionProps) {
  const [open, setOpen] = useState<number | null>(0);
  const reduceMotion = useReducedMotion();
  const baseId = useId();

  const toggle = (index: number) => {
    const next = open === index ? null : index;
    setOpen(next);
    onOpenChange?.(next);
  };

  return (
    <div className="space-y-3">
      {items.map((item, index) => {
        const isOpen = open === index;
        const panelId = `${baseId}-panel-${index}`;
        const buttonId = `${baseId}-button-${index}`;

        return (
          <div
            key={item.question}
            className={cn(
              "sky-card overflow-hidden rounded-[0.95rem] transition-colors",
              isOpen && "border-[rgba(35,136,255,0.24)]"
            )}
          >
            <h3>
              <button
                id={buttonId}
                type="button"
                className="flex w-full items-center justify-between gap-4 px-5 py-[1.1rem] text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--ar-sky)]/35 sm:px-6 2xl:px-7 2xl:py-[1.35rem]"
                aria-expanded={isOpen}
                aria-controls={panelId}
                onClick={() => toggle(index)}
              >
                <span className="text-[1.02rem] font-medium text-[var(--ar-black)] 2xl:text-[1.12rem]">
                  {item.question}
                </span>
                <span
                  className={cn(
                    "flex h-7 w-7 shrink-0 items-center justify-center rounded-full transition-colors",
                    isOpen
                      ? "bg-[image:var(--ar-accent-gradient)] text-white"
                      : "bg-black/[0.04] text-[var(--ar-stone)]"
                  )}
                  aria-hidden
                >
                  <ChevronDown
                    size={15}
                    className={cn("transition-transform duration-300", isOpen && "rotate-180")}
                  />
                </span>
              </button>
            </h3>
            <AnimatePresence initial={false}>
              {isOpen ? (
                <motion.div
                  id={panelId}
                  role="region"
                  aria-labelledby={buttonId}
                  initial={reduceMotion ? false : { height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={reduceMotion ? undefined : { height: 0, opacity: 0 }}
                  transition={{ duration: reduceMotion ? 0 : 0.28, ease: [0.22, 1, 0.36, 1] }}
                  className="overflow-hidden"
                >
                  <p className="px-5 pb-5 text-[0.95rem] leading-relaxed text-[var(--ar-mist)] sm:px-6 2xl:px-7 2xl:pb-6 2xl:text-[1.05rem]">
                    {item.answer}
                  </p>
                </motion.div>
              ) : null}
            </AnimatePresence>
          </div>
        );
      })}
    </div>
  );
}
