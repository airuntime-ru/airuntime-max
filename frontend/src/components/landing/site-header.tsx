"use client";

import Link from "next/link";
import { useEffect, useId, useState } from "react";
import { Menu, X } from "lucide-react";

import { Logo } from "@/components/brand/logo";
import { LOGIN_HREF, navItems } from "@/lib/landing/content";
import { cn } from "@/lib/cn";

export function SiteHeader() {
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const menuId = useId();

  useEffect(() => {
    // rAF-throttled so a fast scroll queues at most one state check per frame.
    let frame = 0;
    const onScroll = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        setScrolled(window.scrollY > 12);
      });
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open]);

  // Floating pill rather than a full-width bar: the page alternates light and dark
  // sections, and a translucent dark bar reads as a muddy grey slab over the light ones.
  return (
    <header className="fixed inset-x-0 top-0 z-40 px-3 sm:px-5">
      {/* Dim layer behind the pill (not below it in the page): tap-outside-to-close, and it
          stops page content showing through the gap beside the floating panel. */}
      {open ? (
        <button
          type="button"
          aria-label="Закрыть меню"
          onClick={() => setOpen(false)}
          className="fixed inset-0 -z-10 bg-[rgba(4,8,20,0.6)] backdrop-blur-sm lg:hidden"
        />
      ) : null}
      <div
        className={cn(
          "landing-measure flex h-16 items-center justify-between gap-4 border px-4 transition-all duration-300 sm:px-6 2xl:h-[4.25rem] 2xl:px-7",
          scrolled || open
            ? "mt-3 rounded-full border-white/10 bg-[rgba(9,14,28,0.74)] shadow-[0_20px_50px_-26px_rgba(0,0,0,0.95)] backdrop-blur-xl"
            : "mt-1 border-transparent bg-transparent"
        )}
      >
        {/* The wordmark plus a CTA plus a burger does not fit a 360px pill - mark only there. */}
        <span className="sm:hidden">
          <Logo href="/" variant="mark" theme="light" size="sm" priority />
        </span>
        <span className="hidden sm:inline-flex">
          <Logo href="/" variant="full" theme="light" size="sm" priority />
        </span>

        <nav className="hidden items-center gap-1 lg:flex" aria-label="Основная навигация">
          {navItems.map((item) => (
            <a
              key={item.href}
              href={item.href}
              className="rounded-full px-4 py-2 text-sm font-medium text-white/65 transition-colors hover:bg-white/[0.08] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40 2xl:px-5 2xl:text-[0.95rem]"
            >
              {item.label}
            </a>
          ))}
        </nav>

        <div className="flex items-center gap-2 sm:gap-3">
          <Link
            href={LOGIN_HREF}
            className="hidden rounded-full px-4 py-2 text-sm font-medium text-white/65 transition-colors hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40 sm:inline-flex 2xl:text-[0.95rem]"
          >
            Войти
          </Link>
          <Link
            href={LOGIN_HREF}
            className="btn-glow inline-flex h-10 items-center justify-center whitespace-nowrap rounded-full px-3.5 text-[0.82rem] font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/50 sm:px-5 sm:text-sm 2xl:h-11 2xl:px-6 2xl:text-[0.95rem]"
          >
            Создать проект
          </Link>
          <button
            type="button"
            className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-white/12 bg-white/[0.06] text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40 lg:hidden"
            aria-expanded={open}
            aria-controls={menuId}
            aria-label={open ? "Закрыть меню" : "Открыть меню"}
            onClick={() => setOpen((value) => !value)}
          >
            {open ? <X size={18} aria-hidden /> : <Menu size={18} aria-hidden />}
          </button>
        </div>
      </div>

      {open ? (
        <div
          id={menuId}
          className="landing-measure mt-2 overflow-hidden rounded-[1.25rem] border border-white/10 bg-[rgba(9,14,28,0.96)] shadow-[0_20px_50px_-26px_rgba(0,0,0,0.95)] backdrop-blur-xl lg:hidden"
        >
          <nav
            className="flex flex-col gap-1 p-3"
            aria-label="Мобильная навигация"
          >
            {navItems.map((item) => (
              <a
                key={item.href}
                href={item.href}
                className="rounded-[0.7rem] px-3 py-3 text-base font-medium text-white/80 hover:bg-white/[0.07] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40"
                onClick={() => setOpen(false)}
              >
                {item.label}
              </a>
            ))}
            <Link
              href={LOGIN_HREF}
              className="rounded-[0.7rem] px-3 py-3 text-base font-medium text-[#8ec4ff] hover:bg-white/[0.07] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40"
              onClick={() => setOpen(false)}
            >
              Войти
            </Link>
          </nav>
        </div>
      ) : null}
    </header>
  );
}
