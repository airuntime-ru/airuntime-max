"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/cn";

export function Tabs({
  items,
  sticky = false,
}: {
  items: { href: string; label: string }[];
  sticky?: boolean;
}) {
  const pathname = usePathname();

  return (
    <nav
      aria-label="Разделы проекта"
      className={cn(
        "flex gap-1 overflow-x-auto py-1.5 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden",
        // Sits just under the floating top bar, which is fixed at every width (see AppTopBar).
        sticky &&
          "sticky top-[4.9rem] z-20 -mx-3 border-b border-[var(--ar-border)] bg-[rgba(246,249,253,0.88)] px-3 backdrop-blur-md sm:-mx-4 sm:px-4 lg:-mx-0 lg:px-0"
      )}
    >
      {items.map((item) => {
        const active = pathname === item.href;
        return (
          <Link
            key={item.href}
            href={item.href}
            className={cn(
              "shrink-0 whitespace-nowrap rounded-full px-3.5 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ar-sky)]/35",
              active
                ? "bg-[var(--ar-black)] text-white"
                : "text-[var(--ar-stone)] hover:bg-black/[0.04] hover:text-[var(--ar-graphite)]"
            )}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
