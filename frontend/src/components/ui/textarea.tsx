import { cn } from "@/lib/cn";

export function Textarea({ className, ...props }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        "min-h-[108px] w-full rounded-[var(--ar-radius-sm)] border border-black/15 bg-white p-3 text-sm text-[var(--ar-black)] outline-none placeholder:text-[var(--ar-stone)] focus:border-black/30 focus:ring-2 focus:ring-black/10",
        className
      )}
      {...props}
    />
  );
}
