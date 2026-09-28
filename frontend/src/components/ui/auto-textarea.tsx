"use client";

import { useEffect, useRef } from "react";

import { cn } from "@/lib/cn";

type AutoTextareaProps = React.TextareaHTMLAttributes<HTMLTextAreaElement>;

export function AutoTextarea({ className, value, onChange, ...props }: AutoTextareaProps) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [value]);

  return (
    <textarea
      ref={ref}
      rows={1}
      value={value}
      onChange={onChange}
      className={cn(
        "min-h-[44px] w-full resize-none rounded-[var(--ar-radius-sm)] border border-[var(--ar-border)] bg-white/90 px-4 py-2.5 text-sm leading-6 text-[var(--ar-black)] shadow-sm shadow-sky-950/5 placeholder:text-[var(--ar-stone)] focus:border-[var(--ar-border-strong)] focus:bg-white focus:outline-none focus:ring-2 focus:ring-[var(--ar-sky)]/15",
        className
      )}
      {...props}
    />
  );
}
