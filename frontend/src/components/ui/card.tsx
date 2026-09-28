import { cn } from "@/lib/cn";

export function Card({
  className,
  hover = true,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & { hover?: boolean }) {
  return (
    <div
      className={cn(
        "sky-card rounded-[0.95rem] p-5",
        hover && "sky-card-hover",
        className
      )}
      {...props}
    />
  );
}
