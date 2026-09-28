"use client";

import { useEffect, useRef } from "react";

import { cn } from "@/lib/cn";

const LENGTH = 6;

type OtpInputProps = {
  value: string;
  onChange: (value: string) => void;
  onComplete?: (value: string) => void;
  disabled?: boolean;
  autoFocus?: boolean;
};

export function OtpInput({ value, onChange, onComplete, disabled, autoFocus }: OtpInputProps) {
  const inputsRef = useRef<Array<HTMLInputElement | null>>([]);
  const digits = Array.from({ length: LENGTH }, (_, index) => value[index] ?? "");

  useEffect(() => {
    if (!autoFocus) return;
    inputsRef.current[0]?.focus();
  }, [autoFocus]);

  const updateValue = (nextDigits: string[]) => {
    const next = nextDigits.join("").slice(0, LENGTH);
    onChange(next);
    if (next.length === LENGTH) onComplete?.(next);
  };

  const focusAt = (index: number) => {
    const target = Math.max(0, Math.min(LENGTH - 1, index));
    inputsRef.current[target]?.focus();
  };

  const onDigitChange = (index: number, raw: string) => {
    const chunk = raw.replace(/\D/g, "");
    if (!chunk) {
      const next = [...digits];
      next[index] = "";
      updateValue(next);
      return;
    }

    if (chunk.length > 1) {
      const next = [...digits];
      chunk
        .slice(0, LENGTH - index)
        .split("")
        .forEach((digit, offset) => {
          next[index + offset] = digit;
        });
      updateValue(next);
      focusAt(index + Math.min(chunk.length, LENGTH - index));
      return;
    }

    const next = [...digits];
    next[index] = chunk;
    updateValue(next);
    if (index < LENGTH - 1) focusAt(index + 1);
  };

  const onKeyDown = (index: number, event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Backspace") {
      event.preventDefault();
      const next = [...digits];
      if (next[index]) {
        next[index] = "";
        updateValue(next);
        return;
      }
      if (index > 0) {
        next[index - 1] = "";
        updateValue(next);
        focusAt(index - 1);
      }
      return;
    }

    if (event.key === "ArrowLeft") {
      event.preventDefault();
      focusAt(index - 1);
      return;
    }

    if (event.key === "ArrowRight") {
      event.preventDefault();
      focusAt(index + 1);
      return;
    }

    if (event.key === "Enter") {
      event.preventDefault();
      if (value.length === LENGTH) onComplete?.(value);
    }
  };

  const onPaste = (event: React.ClipboardEvent<HTMLInputElement>) => {
    event.preventDefault();
    const pasted = event.clipboardData.getData("text").replace(/\D/g, "").slice(0, LENGTH);
    if (!pasted) return;
    const next = Array.from({ length: LENGTH }, (_, index) => pasted[index] ?? "");
    updateValue(next);
    focusAt(Math.min(pasted.length, LENGTH - 1));
  };

  return (
    <div className="flex justify-center gap-2 sm:gap-2.5">
      {digits.map((digit, index) => (
        <input
          key={index}
          ref={(node) => {
            inputsRef.current[index] = node;
          }}
          type="text"
          inputMode="numeric"
          autoComplete={index === 0 ? "one-time-code" : "off"}
          aria-label={`Цифра ${index + 1}`}
          maxLength={1}
          value={digit}
          disabled={disabled}
          onChange={(event) => onDigitChange(index, event.target.value)}
          onKeyDown={(event) => onKeyDown(index, event)}
          onPaste={onPaste}
          onFocus={(event) => event.currentTarget.select()}
          className={cn(
            "h-12 w-11 rounded-[var(--ar-radius-sm)] border border-[var(--ar-border)] bg-white text-center text-xl font-semibold tabular-nums text-[var(--ar-black)] shadow-sm shadow-sky-950/5 outline-none transition-all sm:h-14 sm:w-12 sm:text-2xl",
            "focus:border-[var(--ar-border-strong)] focus:ring-2 focus:ring-[var(--ar-sky)]/15",
            disabled && "opacity-50"
          )}
        />
      ))}
    </div>
  );
}
