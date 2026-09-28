"use client";

import { useEffect, useRef, useState, type ElementType, type ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * Fades content up the first time it scrolls into view.
 *
 * One shared IntersectionObserver instead of a scroll listener, and the observer
 * unsubscribes each element as soon as it has played - a landing with ~40 revealed
 * blocks ends up with zero live observers once you've scrolled past them.
 */
const observed = new WeakMap<Element, () => void>();
let observer: IntersectionObserver | null = null;

function getObserver() {
  if (observer) return observer;
  observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        observed.get(entry.target)?.();
        observed.delete(entry.target);
        observer?.unobserve(entry.target);
      }
    },
    { rootMargin: "0px 0px -12% 0px", threshold: 0.08 }
  );
  return observer;
}

type RevealProps = {
  children: ReactNode;
  className?: string;
  /** Stagger within a group, in ms. */
  delay?: number;
  as?: ElementType;
};

export function Reveal({ children, className, delay = 0, as: Tag = "div" }: RevealProps) {
  const ref = useRef<HTMLElement>(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const node = ref.current;
    if (!node || shown) return undefined;
    // Without IntersectionObserver nothing would ever un-hide the content, so show it at once.
    if (typeof IntersectionObserver === "undefined") {
      const timer = window.setTimeout(() => setShown(true), 0);
      return () => window.clearTimeout(timer);
    }
    observed.set(node, () => setShown(true));
    getObserver().observe(node);
    return () => {
      observed.delete(node);
      getObserver().unobserve(node);
    };
  }, [shown]);

  return (
    <Tag
      ref={ref}
      className={cn("reveal", shown && "is-in", className)}
      style={delay ? ({ "--reveal-delay": `${delay}ms` } as React.CSSProperties) : undefined}
    >
      {children}
    </Tag>
  );
}
