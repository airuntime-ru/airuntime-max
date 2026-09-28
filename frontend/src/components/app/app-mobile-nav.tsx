"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { FolderKanban, LogOut, Menu, Plus, User, X } from "lucide-react";
import { useEffect, useId, useState } from "react";

import { useCreateProject } from "@/components/app/create-project-context";
import { cn } from "@/lib/cn";

const primaryNav = [
  {
    href: "/app",
    label: "Проекты",
    icon: FolderKanban,
    match: (path: string) => path === "/app" || path.startsWith("/app/projects"),
  },
  {
    href: "/app/profile",
    label: "Профиль",
    icon: User,
    match: (path: string) => path === "/app/profile",
  },
];

/**
 * Thumb-reach navigation for small screens. The floating top bar carries the brand and the
 * credit balance at every width, so this bar only has to handle section switching.
 */
export function AppMobileNav({ onLogout }: { onLogout: () => void }) {
  const pathname = usePathname();
  const { openCreateProject } = useCreateProject();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuId = useId();

  useEffect(() => {
    if (!menuOpen) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [menuOpen]);

  return (
    <>
      <nav className="fixed inset-x-0 bottom-0 z-[30] border-t border-black/[0.06] bg-white/85 px-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] pt-2 backdrop-blur-xl lg:hidden">
        <div className="mx-auto flex max-w-lg items-center justify-around gap-1">
          {primaryNav.map((item) => {
            const active = item.match(pathname);
            return (
              <Link
                key={item.href}
                href={item.href}
                data-tour={item.href === "/app" ? "nav-projects" : undefined}
                className={cn(
                  "flex min-h-11 min-w-0 flex-1 flex-col items-center justify-center gap-1 rounded-[0.7rem] px-2 text-[0.68rem] font-semibold transition-colors",
                  active
                    ? "bg-[var(--ar-black)] text-white"
                    : "text-[var(--ar-stone)] hover:bg-black/[0.04]"
                )}
              >
                <item.icon size={18} aria-hidden />
                <span className="truncate">{item.label}</span>
              </Link>
            );
          })}
          <button
            type="button"
            onClick={openCreateProject}
            aria-label="Новый проект"
            className="btn-glow flex h-11 min-w-0 flex-1 flex-col items-center justify-center gap-1 rounded-[0.7rem] px-2 text-[0.68rem] font-semibold"
          >
            <Plus size={18} aria-hidden />
            <span className="truncate">Создать</span>
          </button>
          <button
            type="button"
            onClick={() => setMenuOpen(true)}
            aria-expanded={menuOpen}
            aria-controls={menuId}
            className="flex min-h-11 min-w-0 flex-1 flex-col items-center justify-center gap-1 rounded-[0.7rem] px-2 text-[0.68rem] font-semibold text-[var(--ar-stone)]"
          >
            <Menu size={18} aria-hidden />
            <span>Ещё</span>
          </button>
        </div>
      </nav>

      {menuOpen ? (
        <div className="fixed inset-0 z-[40] lg:hidden">
          <button
            type="button"
            className="absolute inset-0 bg-[rgba(4,8,20,0.45)] backdrop-blur-[3px]"
            aria-label="Закрыть меню"
            onClick={() => setMenuOpen(false)}
          />
          <div
            id={menuId}
            className="absolute inset-x-0 bottom-0 rounded-t-[1.25rem] border-t border-black/[0.06] bg-white p-4 pb-[max(1rem,env(safe-area-inset-bottom))]"
            role="dialog"
            aria-modal="true"
            aria-label="Меню"
          >
            <div className="mb-4 flex items-center justify-between">
              <p className="text-sm font-semibold text-[var(--ar-black)]">Меню</p>
              <button
                type="button"
                onClick={() => setMenuOpen(false)}
                className="inline-flex h-10 w-10 items-center justify-center rounded-full text-[var(--ar-stone)] hover:bg-black/[0.05] hover:text-[var(--ar-black)]"
                aria-label="Закрыть"
              >
                <X size={18} aria-hidden />
              </button>
            </div>
            <button
              type="button"
              onClick={() => {
                setMenuOpen(false);
                onLogout();
              }}
              className="flex min-h-11 w-full items-center gap-3 rounded-[0.7rem] px-3 text-left text-sm font-medium text-[var(--ar-graphite)] transition-colors hover:bg-black/[0.04] hover:text-[var(--ar-black)]"
            >
              <LogOut size={18} aria-hidden />
              Выйти
            </button>
          </div>
        </div>
      ) : null}
    </>
  );
}
