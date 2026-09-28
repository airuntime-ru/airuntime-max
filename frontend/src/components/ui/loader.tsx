import { cn } from "@/lib/cn";

export function Loader({ label = "Загружаем" }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 text-[var(--ar-mist)]" role="status" aria-live="polite">
      <span className="ai-typing" aria-hidden>
        <span />
        <span />
        <span />
      </span>
      <span className="text-sm">{label}</span>
    </div>
  );
}

export function PageLoader() {
  return (
    <div className="flex min-h-[40vh] items-center justify-center">
      <Loader />
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
  className,
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("sky-card rounded-[0.95rem] px-6 py-12 text-center", className)}>
      <span
        className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-[image:var(--ar-accent-gradient-soft)] text-xl"
        aria-hidden
      >
        ✦
      </span>
      <h3 className="mt-5 text-lg font-semibold text-[var(--ar-black)]">{title}</h3>
      <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-[var(--ar-mist)]">
        {description}
      </p>
      {action ? <div className="mt-6">{action}</div> : null}
    </div>
  );
}
