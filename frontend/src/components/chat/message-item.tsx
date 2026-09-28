"use client";

import { memo, useEffect, useState, type ComponentType } from "react";
import { Activity, CheckCircle2, Loader2, Sparkles } from "lucide-react";

import { ExecutionReportShell } from "@/components/chat/execution-report";
import { cn } from "@/lib/cn";
import type { AgentStatus, ChatMessage } from "@/lib/chat-stream-runtime";

type RichMarkdownProps = { children: string };
let richMarkdownPromise: Promise<ComponentType<RichMarkdownProps>> | null = null;

function loadRichMarkdown() {
  if (!richMarkdownPromise) {
    richMarkdownPromise = import("@/components/chat/rich-markdown").then((mod) => mod.RichMarkdown);
  }
  return richMarkdownPromise;
}

function AssistantMarkdown({ content }: { content: string }) {
  const [RichMarkdown, setRichMarkdown] = useState<ComponentType<RichMarkdownProps> | null>(null);

  useEffect(() => {
    let active = true;
    void loadRichMarkdown().then((Comp) => {
      if (active) setRichMarkdown(() => Comp);
    });
    return () => {
      active = false;
    };
  }, []);

  // Keep the finished turn readable while highlight.js is still loading - plain text, no freeze.
  if (!RichMarkdown) {
    return <div className="whitespace-pre-wrap text-[15px] leading-[1.55] text-[var(--ar-black)]">{content}</div>;
  }
  return <RichMarkdown>{content}</RichMarkdown>;
}

export const REPORT_HEADING = "### Отчёт о выполнении";

export function splitReport(content: string): { main: string; report: string } {
  const idx = content.lastIndexOf(REPORT_HEADING);
  if (idx === -1) return { main: content, report: "" };
  return {
    main: content.slice(0, idx).trimEnd(),
    report: content.slice(idx + REPORT_HEADING.length).trim(),
  };
}

function AiTypingIndicator() {
  return (
    <div className="flex items-center gap-2 py-1 text-sm text-[var(--ar-stone)]">
      <span className="inline-flex items-center gap-1" aria-hidden>
        <span className="h-1 w-1 animate-pulse rounded-full bg-[var(--ar-stone)]" />
        <span className="h-1 w-1 animate-pulse rounded-full bg-[var(--ar-stone)] [animation-delay:120ms]" />
        <span className="h-1 w-1 animate-pulse rounded-full bg-[var(--ar-stone)] [animation-delay:220ms]" />
      </span>
    </div>
  );
}

export const MessageBody = memo(function MessageBody({
  message,
  isStreaming,
}: {
  message: ChatMessage;
  isStreaming: boolean;
}) {
  if (message.role === "assistant" && isStreaming && !message.content) {
    return <AiTypingIndicator />;
  }

  if (message.role === "assistant") {
    const { main, report } = splitReport(message.content);
    if (isStreaming) {
      return (
        <div className="whitespace-pre-wrap text-[15px] leading-[1.55] text-[var(--ar-black)]">
          {main || message.content}
        </div>
      );
    }
    return (
      <div className="cursor-chat-assistant prose-chat prose-chat-cursor text-[15px] text-[var(--ar-black)]">
        <AssistantMarkdown content={main} />
        {report ? (
          <ExecutionReportShell collapsible>
            <div className="prose-chat prose-chat-compact scrollbar-airy max-h-64 overflow-y-auto px-3 py-2 text-[var(--ar-mist)]">
              <AssistantMarkdown content={report} />
            </div>
          </ExecutionReportShell>
        ) : null}
      </div>
    );
  }

  return (
    <p className="whitespace-pre-wrap text-[15px] leading-[1.55] text-[var(--ar-black)]">{message.content}</p>
  );
});

export const MessageItem = memo(function MessageItem({
  message,
  isStreaming,
}: {
  message: ChatMessage;
  isStreaming: boolean;
}) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end" style={{ contentVisibility: "auto", containIntrinsicSize: "auto 72px" }}>
        <div className="max-w-[min(100%,42rem)] rounded-[var(--ar-radius-md)] bg-[#eef1f6] px-4 py-2.5">
          {message.attachments?.length ? (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {message.attachments.map((file) => (
                <a
                  key={file.id}
                  href={file.download_url ?? "#"}
                  target="_blank"
                  rel="noreferrer"
                  className="rounded-md bg-white/80 px-2 py-0.5 text-xs text-[var(--ar-mist)] hover:underline"
                >
                  {file.original_filename}
                </a>
              ))}
            </div>
          ) : null}
          <MessageBody message={message} isStreaming={false} />
        </div>
      </div>
    );
  }

  return (
    <div
      className="w-full"
      style={
        isStreaming
          ? undefined
          : { contentVisibility: "auto", containIntrinsicSize: "auto 96px" }
      }
    >
      {/* Assistant turns are full-width plain text (no bubble), so without an author line it
          is hard to see where the agent stops and the next user message starts. */}
      <div className="mb-1.5 flex items-center gap-2">
        <span
          className="flex h-5 w-5 items-center justify-center rounded-full bg-[image:var(--ar-accent-gradient)] text-white"
          aria-hidden
        >
          <Sparkles size={11} />
        </span>
        <span className="text-xs font-semibold text-[var(--ar-stone)]">AIRuntime</span>
      </div>
      <MessageBody message={message} isStreaming={isStreaming} />
    </div>
  );
});

function elapsedSecondsSince(startedAt: number): number {
  return Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
}

function useElapsedSeconds(startedAt: number | null, active: boolean): number | null {
  const [elapsed, setElapsed] = useState<number | null>(null);
  useEffect(() => {
    if (!active || !startedAt) return undefined;
    const id = window.setInterval(() => setElapsed(elapsedSecondsSince(startedAt)), 1000);
    return () => window.clearInterval(id);
  }, [active, startedAt]);
  return active ? elapsed : null;
}

function formatElapsed(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes > 0 ? `${minutes}м ${seconds}с` : `${seconds}с`;
}

function estimateTokens(chars: number): number {
  return Math.max(0, Math.round(chars / 2.2));
}

const AGENT_STEPS = ["thinking", "context", "tool", "verify", "module", "version", "deploy", "done"] as const;

export function AgentStatusPanel({
  status,
  projectId,
  turnStartedAt,
  liveChars,
  onContinue,
}: {
  status: AgentStatus;
  projectId: string;
  turnStartedAt: number | null;
  liveChars: number;
  onContinue: () => void;
}) {
  const needsSecret = status.phase === "needs_configuration";
  const waiting = status.state === "waiting" || status.phase === "questions" || needsSecret;
  const done = status.state === "done" && !waiting;
  const error = !waiting && (status.state === "error" || status.phase === "error");
  const currentIndex = Math.max(0, AGENT_STEPS.indexOf(status.phase as (typeof AGENT_STEPS)[number]));
  const needsToken = needsSecret && status.label.includes("TELEGRAM_BOT_TOKEN");
  const secretsHref = `/app/projects/${projectId}/settings#secrets`;
  const helpHref = `/help/telegram-token?projectId=${encodeURIComponent(projectId)}`;
  const isRunning = status.state === "running" && !waiting;
  const elapsedSeconds = useElapsedSeconds(turnStartedAt, isRunning);

  return (
    <div
      className={cn(
        "sticky bottom-3 z-10 mb-5 overflow-hidden rounded-[var(--ar-radius-md)] border px-4 py-3",
        error
          ? "border-rose-200 bg-rose-50"
          : waiting
            ? "border-amber-200 bg-amber-50"
            : "border-sky-200 bg-white"
      )}
      aria-live="polite"
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <span
            className={cn(
              "flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--ar-radius-sm)]",
              error
                ? "bg-rose-100 text-rose-600"
                : waiting
                  ? "bg-amber-100 text-amber-700"
                  : done
                    ? "bg-emerald-50 text-emerald-600"
                    : "bg-sky-50 text-[var(--ar-sky)]"
            )}
          >
            {done ? (
              <CheckCircle2 size={18} aria-hidden />
            ) : error || waiting ? (
              <Activity size={18} aria-hidden />
            ) : (
              <Loader2 size={18} className="animate-spin" aria-hidden />
            )}
          </span>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-[var(--ar-black)]">{status.label}</p>
            <p className="text-xs text-[var(--ar-stone)]">
              {error
                ? "Запрос не выполнен — исправьте причину и отправьте сообщение снова"
                : needsToken
                  ? "Добавьте токен в секреты проекта — после этого запуск продолжится"
                  : waiting
                    ? "Ответьте в чат, и агент продолжит сборку"
                    : done
                      ? "Статус подтверждён по деплою"
                      : "Статус сборки обновляется в реальном времени"}
            </p>
            {isRunning && elapsedSeconds !== null ? (
              <p className="mt-0.5 text-[11px] tabular-nums text-[var(--ar-stone)]">
                {formatElapsed(elapsedSeconds)}
                {liveChars > 0 ? ` · ≈${estimateTokens(liveChars).toLocaleString("ru-RU")} токенов` : ""}
              </p>
            ) : null}
          </div>
        </div>
        <span className="hidden rounded-[0.5rem] border border-black/10 bg-white px-2.5 py-1 text-xs font-medium text-[var(--ar-mist)] sm:inline-flex">
          {error
            ? "ошибка"
            : needsToken
              ? "нужен токен"
              : waiting
                ? "ожидание"
                : done
                  ? "готово"
                  : "агент работает"}
        </span>
      </div>

      {!error && !waiting ? (
        <div
          className="mt-3 grid gap-1.5"
          style={{ gridTemplateColumns: `repeat(${AGENT_STEPS.length}, minmax(0, 1fr))` }}
        >
          {AGENT_STEPS.map((step, index) => (
            <span
              key={step}
              className={cn(
                "h-1.5 rounded-full transition-colors",
                index <= currentIndex ? "bg-[var(--ar-sky)]" : "bg-black/8",
                status.state === "running" && index === currentIndex && "animate-pulse"
              )}
            />
          ))}
        </div>
      ) : null}

      {needsSecret ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <a
            href={secretsHref}
            className="inline-flex min-h-9 items-center rounded-[0.5rem] bg-[var(--ar-black)] px-3 text-xs font-medium text-white hover:bg-black/85"
          >
            В секреты
          </a>
          {needsToken ? (
            <a
              href={helpHref}
              className="inline-flex min-h-9 items-center rounded-[0.5rem] border border-rose-200 bg-white px-3 text-xs font-medium text-rose-700 hover:bg-rose-50"
            >
              Как получить токен
            </a>
          ) : null}
          <button
            type="button"
            onClick={onContinue}
            className="inline-flex min-h-9 items-center rounded-[0.5rem] border border-emerald-200 bg-emerald-50 px-3 text-xs font-medium text-emerald-700 hover:bg-emerald-100"
          >
            Настроил секрет, продолжай
          </button>
        </div>
      ) : null}
    </div>
  );
}
