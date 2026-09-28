import { cn } from "@/lib/cn";

/**
 * The dot inherits the badge's own text colour: callers tint the badge per status
 * (green "готово", red "ошибка"), and a fixed brand-gradient dot contradicted every one
 * of them.
 */
export function Badge({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border border-black/10 bg-black/[0.03] px-2.5 py-1 text-xs font-semibold text-[var(--ar-mist)]",
        className
      )}
    >
      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-current opacity-70" aria-hidden />
      {children}
    </span>
  );
}
