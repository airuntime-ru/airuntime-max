"use client";

import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/cn";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-[0.7rem] text-sm font-semibold tracking-[-0.01em] transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ar-sky)]/35 disabled:pointer-events-none disabled:opacity-50 active:scale-[0.99]",
  {
    variants: {
      variant: {
        default: "btn-glow border border-transparent",
        ghost: "text-[var(--ar-graphite)] hover:bg-black/5",
        accent: "btn-glow border border-transparent",
        outline:
          "border border-black/10 bg-white text-[var(--ar-graphite)] shadow-[0_1px_2px_rgba(15,23,42,0.04)] hover:border-[var(--ar-sky)]/35 hover:bg-[rgba(31,122,239,0.04)] hover:text-[var(--ar-black)]",
        ink: "border border-transparent bg-[var(--ar-black)] text-white hover:bg-[#16223a]",
      },
      size: {
        default: "h-10 px-4 py-2",
        lg: "h-11 px-6 text-base",
        sm: "h-8 px-3 text-xs",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => {
    return <button className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />;
  }
);
Button.displayName = "Button";

export { Button, buttonVariants };
