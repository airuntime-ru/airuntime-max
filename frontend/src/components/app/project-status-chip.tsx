import { cn } from "@/lib/cn";
import {
  projectStatusLabel,
  projectStatusTone,
  type ProjectStatusTone,
} from "@/lib/project-status";

const TONE_CHIP: Record<ProjectStatusTone, string> = {
  live: "border-emerald-500/25 bg-emerald-50 text-emerald-700",
  progress: "border-sky-500/25 bg-sky-50 text-sky-700",
  warn: "border-amber-500/30 bg-amber-50 text-amber-800",
  danger: "border-rose-500/25 bg-rose-50 text-rose-700",
  idle: "border-black/10 bg-black/[0.03] text-[var(--ar-mist)]",
};

const TONE_DOT: Record<ProjectStatusTone, string> = {
  live: "bg-emerald-500 text-emerald-500",
  progress: "bg-sky-500 text-sky-500",
  warn: "bg-amber-500 text-amber-500",
  danger: "bg-rose-500 text-rose-500",
  idle: "bg-[var(--ar-stone)] text-[var(--ar-stone)]",
};

/** Status pill shared by the project list, the project header and the overview cards. */
export function ProjectStatusChip({
  status,
  className,
}: {
  status: string;
  className?: string;
}) {
  const tone = projectStatusTone(status);
  const animate = tone === "live" || tone === "progress";

  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-xs font-semibold",
        TONE_CHIP[tone],
        className
      )}
    >
      <span
        className={cn(
          "h-1.5 w-1.5 shrink-0 rounded-full",
          TONE_DOT[tone],
          animate && "live-dot"
        )}
        aria-hidden
      />
      {projectStatusLabel(status)}
    </span>
  );
}
