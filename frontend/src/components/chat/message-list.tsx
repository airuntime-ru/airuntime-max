"use client";

import { useEffect, useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Sparkles } from "lucide-react";

import { MessageItem } from "@/components/chat/message-item";
import type { ChatMessage } from "@/lib/chat-stream-runtime";

const ESTIMATE_PX = 120;

const STARTER_PROMPTS = [
  "Сделай сайт автосервиса с записью",
  "Нужен лендинг для запуска курса",
  "Бот принимает заявки и шлёт напоминания",
] as const;

type MessageListProps = {
  messages: ChatMessage[];
  loading: boolean;
  stickToBottom: boolean;
  onStickChange: (nearBottom: boolean) => void;
  bottomSlot?: React.ReactNode;
  /** Starts a turn from the empty-state starter chips (or fills the composer if busy). */
  onPickPrompt?: (prompt: string) => void;
};

export function MessageList({
  messages,
  loading,
  stickToBottom,
  onStickChange,
  bottomSlot,
  onPickPrompt,
}: MessageListProps) {
  const parentRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => ESTIMATE_PX,
    overscan: 4,
    measureElement:
      typeof window !== "undefined" && !navigator.userAgent.includes("Firefox")
        ? (element) => element.getBoundingClientRect().height
        : undefined,
  });

  useEffect(() => {
    const node = parentRef.current;
    if (!node) return undefined;
    const onScroll = () => {
      const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
      onStickChange(distance < 140);
    };
    node.addEventListener("scroll", onScroll, { passive: true });
    return () => node.removeEventListener("scroll", onScroll);
  }, [onStickChange]);

  const lastContentLen = messages[messages.length - 1]?.content?.length ?? 0;

  useEffect(() => {
    if (!stickToBottom || messages.length === 0) return;
    // Streaming used to scroll on every content length change with a double rAF + layout read;
    // throttle so measure/scroll work stays off the hot path while text is pouring in.
    const timer = window.setTimeout(() => {
      virtualizer.scrollToIndex(messages.length - 1, { align: "end" });
    }, 120);
    return () => window.clearTimeout(timer);
  }, [stickToBottom, messages.length, lastContentLen, loading, virtualizer]);

  if (messages.length === 0) {
    return (
      <div ref={parentRef} className="flex-1 overflow-y-auto">
        <div className="mx-auto flex min-h-[40vh] w-full max-w-3xl flex-col items-center justify-center px-4 text-center sm:px-6">
          <span
            className="flex h-11 w-11 items-center justify-center rounded-full bg-[image:var(--ar-accent-gradient)] text-white"
            aria-hidden
          >
            <Sparkles size={20} />
          </span>
          <p className="mt-4 text-lg font-medium text-[var(--ar-black)]">Чем помочь?</p>
          <p className="mt-2 max-w-sm text-sm text-[var(--ar-stone)]">
            Напишите задачу или выберите пример — соберём и задеплоим проект.
          </p>

          {/* This is the screen a brand-new project lands on, and the blocker is not knowing
              what to type. Tapping a prompt starts the turn immediately. */}
          {onPickPrompt ? (
            <ul className="mt-7 flex flex-wrap justify-center gap-2 pb-2">
              {STARTER_PROMPTS.map((prompt, index) => (
                // The third one would sit under the composer on a 390px screen.
                <li key={prompt} className={index === 2 ? "hidden sm:block" : undefined}>
                  <button
                    type="button"
                    onClick={() => onPickPrompt(prompt)}
                    className="rounded-full border border-black/[0.08] bg-white px-4 py-2 text-sm text-[var(--ar-graphite)] transition-colors hover:border-[var(--ar-sky)]/35 hover:bg-[rgba(31,122,239,0.05)] hover:text-[var(--ar-black)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ar-sky)]/35"
                  >
                    {prompt}
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        {bottomSlot}
      </div>
    );
  }

  const items = virtualizer.getVirtualItems();

  return (
    <div ref={parentRef} className="flex-1 overflow-y-auto" data-tour="chat-message-list">
      <div className="mx-auto w-full max-w-3xl px-4 py-6 pb-8 sm:px-6">
        <div
          className="relative w-full"
          style={{ height: `${virtualizer.getTotalSize()}px` }}
        >
          {items.map((item) => {
            const message = messages[item.index];
            const isStreaming =
              loading && item.index === messages.length - 1 && message.role === "assistant";
            return (
              <div
                key={item.key}
                data-index={item.index}
                ref={virtualizer.measureElement}
                className="absolute left-0 top-0 w-full pb-8"
                style={{ transform: `translateY(${item.start}px)` }}
              >
                <MessageItem message={message} isStreaming={isStreaming} />
              </div>
            );
          })}
        </div>
        {bottomSlot}
      </div>
    </div>
  );
}
