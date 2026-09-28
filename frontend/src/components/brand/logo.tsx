"use client";

import Link from "next/link";
import { useId } from "react";

import { cn } from "@/lib/cn";

type LogoVariant = "full" | "mark" | "stacked";
type LogoTheme = "light" | "dark";

const sizes: Record<"sm" | "md" | "lg", { mark: number; text: string }> = {
  sm: { mark: 28, text: "text-[1.05rem]" },
  md: { mark: 36, text: "text-[1.35rem]" },
  lg: { mark: 48, text: "text-[1.72rem]" },
};

type LogoProps = {
  variant?: LogoVariant;
  theme?: LogoTheme;
  size?: keyof typeof sizes;
  className?: string;
  href?: string;
  priority?: boolean;
};

function AirMark({ size, className }: { size: number; className?: string }) {
  const id = useId().replace(/:/g, "");
  const left = `ar-left-${id}`;
  const right = `ar-right-${id}`;
  const cross = `ar-cross-${id}`;
  const shine = `ar-shine-${id}`;
  const glow = `ar-glow-${id}`;

  return (
    <svg
      viewBox="55 45 402 402"
      width={size}
      height={size}
      role="img"
      aria-label="AIRuntime"
      className={cn("block overflow-visible", className)}
    >
      <defs>
        <linearGradient id={left} x1="110" y1="430" x2="280" y2="50" gradientUnits="userSpaceOnUse">
          <stop stopColor="#c7b8ff" stopOpacity="0.9" />
          <stop offset="0.44" stopColor="#8bdfff" stopOpacity="0.96" />
          <stop offset="1" stopColor="#1d8cff" stopOpacity="0.88" />
        </linearGradient>
        <linearGradient id={right} x1="238" y1="76" x2="424" y2="430" gradientUnits="userSpaceOnUse">
          <stop stopColor="#eefbff" stopOpacity="0.96" />
          <stop offset="0.43" stopColor="#50dbe5" stopOpacity="0.92" />
          <stop offset="1" stopColor="#2388ff" stopOpacity="0.9" />
        </linearGradient>
        <linearGradient id={cross} x1="146" y1="330" x2="370" y2="258" gradientUnits="userSpaceOnUse">
          <stop stopColor="#cfc6ff" stopOpacity="0.8" />
          <stop offset="0.55" stopColor="#eaf9ff" stopOpacity="0.88" />
          <stop offset="1" stopColor="#14b8d7" stopOpacity="0.78" />
        </linearGradient>
        <linearGradient id={shine} x1="-90" y1="0" x2="160" y2="0" gradientUnits="userSpaceOnUse">
          <stop stopColor="#ffffff" stopOpacity="0" />
          <stop offset="0.5" stopColor="#ffffff" stopOpacity="0.72" />
          <stop offset="1" stopColor="#ffffff" stopOpacity="0" />
          <animateTransform
            attributeName="gradientTransform"
            type="translate"
            values="-180 0; 560 0; -180 0"
            dur="7s"
            repeatCount="indefinite"
          />
        </linearGradient>
        <filter id={glow} x="-18%" y="-18%" width="136%" height="136%">
          <feGaussianBlur stdDeviation="5" result="blur" />
          <feColorMatrix
            in="blur"
            type="matrix"
            values="0 0 0 0 0.14 0 0 0 0 0.53 0 0 0 0 1 0 0 0 0.35 0"
          />
          <feMerge>
            <feMergeNode />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      <g filter={`url(#${glow})`}>
        <path
          d="M97 404c52-154 88-250 126-297 29-36 63-43 93-29 42 19 77 81 107 183 18 59 28 109 30 132 4 34-16 57-43 57-18 0-32-10-43-31-47-91-91-141-132-151-48-12-91 29-129 122-5 12-9 18-9 14Z"
          fill={`url(#${left})`}
          opacity="0.88"
        />
        <path
          d="M217 109c29-36 63-45 96-31 41 18 78 81 110 183 18 59 28 109 30 132 3 31-13 53-38 56-20 2-36-9-48-31-18-36-36-66-54-90-35-159-69-232-96-219Z"
          fill={`url(#${right})`}
          opacity="0.8"
        />
        <path
          d="M83 407c55-78 107-119 158-126 58-7 112 31 166 115-12 34-36 47-65 27-40-29-74-45-103-47-34-3-70 14-109 51-26 24-52 21-47-20Z"
          fill={`url(#${cross})`}
          opacity="0.9"
        />
        <path
          d="M105 397c52-151 88-244 125-289 28-34 60-41 89-28 40 18 74 78 104 177 17 57 28 106 30 128"
          fill="none"
          stroke="#1293ff"
          strokeOpacity="0.45"
          strokeWidth="5"
          strokeLinecap="round"
        />
        <path
          d="M83 407c55-78 107-119 158-126 58-7 112 31 166 115"
          fill="none"
          stroke="#2fb7ff"
          strokeOpacity="0.5"
          strokeWidth="4"
          strokeLinecap="round"
        />
        <path
          d="M97 404c52-154 88-250 126-297 29-36 63-43 93-29 42 19 77 81 107 183 18 59 28 109 30 132 4 34-16 57-43 57-18 0-32-10-43-31-47-91-91-141-132-151-48-12-91 29-129 122-5 12-9 18-9 14Z"
          fill={`url(#${shine})`}
          opacity="0.72"
        />
      </g>
    </svg>
  );
}

export function Logo({
  variant = "full",
  theme = "dark",
  size = "md",
  className,
  href,
}: LogoProps) {
  const config = sizes[size];
  const textColor = theme === "light" ? "text-white" : "text-[var(--ar-black)]";
  const mark = <AirMark size={config.mark} />;
  const wordmark = (
    <span
      className={cn(
        "select-none font-semibold leading-none tracking-[0.28em]",
        config.text,
        textColor
      )}
    >
      AIRUNTIME
    </span>
  );

  const logo =
    variant === "mark" ? (
      <span className={cn("inline-flex shrink-0 items-center", className)}>{mark}</span>
    ) : variant === "stacked" ? (
      <span className={cn("inline-flex shrink-0 flex-col items-center gap-2", className)}>
        <AirMark size={config.mark * 2.25} />
        {wordmark}
      </span>
    ) : (
      <span className={cn("inline-flex shrink-0 items-center gap-3", className)}>
        {mark}
        {wordmark}
      </span>
    );

  if (href) {
    return (
      <Link href={href} className="inline-flex shrink-0 items-center" aria-label="AIRuntime">
        {logo}
      </Link>
    );
  }

  return logo;
}
