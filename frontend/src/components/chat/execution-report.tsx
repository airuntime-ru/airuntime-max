import type { ReactNode } from "react";
import { Activity, ChevronDown } from "lucide-react";

export function ExecutionReportShell({
  children,
  collapsible = false,
}: {
  children: ReactNode;
  collapsible?: boolean;
}) {
  if (!collapsible) {
    return (
      <div className="mb-3 overflow-hidden rounded-[var(--ar-radius-md)] border border-black/8 bg-[#fafafa] text-[13px]">
        <div className="flex items-center gap-1.5 px-3 py-2 text-[var(--ar-stone)]">
          <Activity size={12} className="shrink-0" aria-hidden />
          <span className="flex-1">Отчёт о выполнении</span>
        </div>
        <div className="border-t border-black/8">{children}</div>
      </div>
    );
  }

  return (
    <details className="group mt-2 overflow-hidden rounded-[var(--ar-radius-md)] border border-black/8 bg-[#fafafa] text-[13px]">
      <summary className="report-summary flex cursor-pointer select-none items-center gap-1.5 px-3 py-2 text-[var(--ar-stone)] hover:text-[var(--ar-mist)]">
        <Activity size={12} className="shrink-0" aria-hidden />
        <span className="flex-1">Отчёт о выполнении</span>
        <ChevronDown
          size={13}
          className="shrink-0 opacity-60 transition-transform group-open:rotate-180"
          aria-hidden
        />
      </summary>
      <div className="border-t border-black/8">{children}</div>
    </details>
  );
}
