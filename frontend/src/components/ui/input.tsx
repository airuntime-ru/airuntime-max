import { cn } from "@/lib/cn";

export function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "h-11 w-full rounded-[var(--ar-radius-sm)] border border-black/15 bg-white px-3 text-sm text-[var(--ar-black)] outline-none placeholder:text-[var(--ar-stone)] focus:border-black/30 focus:ring-2 focus:ring-black/10",
        className
      )}
      {...props}
    />
  );
}
