"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { Activity, AlertCircle, CheckCircle2, Maximize2, Pause, Play, RefreshCw, ShieldCheck, Terminal } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState, PageLoader } from "@/components/ui/loader";
import { Modal } from "@/components/ui/modal";
import { getProjectLogs, type ProjectLogsType } from "@/lib/api";
import { stashPendingRepair } from "@/lib/chat-stream-runtime";
import { cn } from "@/lib/cn";
import { deploymentStatusLabel } from "@/lib/project-status";
import { usePageVisible } from "@/lib/use-page-visible";

const REPAIR_LOG_BUDGET = 100_000;
const MAX_LOG_CHARS = 200_000;
const MAX_PREVIEW_LINES = 800;

function buildRepairLogExcerpt(logs: ProjectLogsType): string {
  const parts: string[] = [];
  if (logs.runtime_error?.trim()) {
    parts.push(logs.runtime_error.trim());
  }
  if (logs.deployment_logs.trim()) {
    parts.push(`--- Деплой / сборка ---\n${logs.deployment_logs.trim()}`);
  }
  if (logs.runtime_logs.trim()) {
    parts.push(`--- Runtime ---\n${logs.runtime_logs.trim()}`);
  }
  const joined = parts.join("\n\n").trim();
  if (!joined) return "";
  return joined.length > REPAIR_LOG_BUDGET ? joined.slice(-REPAIR_LOG_BUDGET) : joined;
}

function clipLog(value: string): string {
  if (value.length <= MAX_LOG_CHARS) return value;
  return `…[обрезано ${value.length - MAX_LOG_CHARS} символов]\n${value.slice(-MAX_LOG_CHARS)}`;
}

function previewLines(value: string): string {
  const clipped = clipLog(value);
  const lines = clipped.split("\n");
  if (lines.length <= MAX_PREVIEW_LINES) return clipped;
  return `…[показаны последние ${MAX_PREVIEW_LINES} строк из ${lines.length}]\n${lines.slice(-MAX_PREVIEW_LINES).join("\n")}`;
}

function LogBlock({
  title,
  hint,
  value,
  onExpand,
}: {
  title: string;
  hint?: string;
  value: string;
  onExpand: () => void;
}) {
  const preview = useMemo(() => previewLines(value), [value]);

  return (
    <section className="overflow-hidden rounded-[var(--ar-radius-md)] border border-black/10 bg-white">
      <div className="flex items-center justify-between gap-2 border-b border-black/8 px-4 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Terminal size={15} className="shrink-0 text-[var(--ar-sky)]" aria-hidden />
            <h2 className="text-sm font-semibold text-[var(--ar-black)]">{title}</h2>
          </div>
          {hint ? <p className="mt-1 pl-6 text-xs text-[var(--ar-stone)]">{hint}</p> : null}
        </div>
        <button
          type="button"
          onClick={onExpand}
          className="inline-flex h-10 w-10 items-center justify-center rounded-[var(--ar-radius-sm)] text-[var(--ar-stone)] hover:bg-black/5 hover:text-[var(--ar-black)]"
          aria-label={`Раскрыть логи: ${title}`}
        >
          <Maximize2 size={14} aria-hidden />
        </button>
      </div>
      <pre className="max-h-[42vh] overflow-auto whitespace-pre-wrap scrollbar-airy bg-[#0d1117] p-4 font-mono text-xs leading-relaxed text-[#c9d1d9]">
        {preview}
      </pre>
    </section>
  );
}

export default function ProjectLogsPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const pageVisible = usePageVisible();
  const [logs, setLogs] = useState<ProjectLogsType | null>(null);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [livePaused, setLivePaused] = useState(false);
  const [expanded, setExpanded] = useState<{ title: string; value: string } | null>(null);

  useEffect(() => {
    let active = true;
    let controller: AbortController | null = null;

    const load = async () => {
      if (!params.id) return;
      controller?.abort();
      controller = new AbortController();
      try {
        setRefreshing(true);
        const next = await getProjectLogs(params.id);
        if (active) {
          setLogs(next);
          setError("");
        }
      } catch (err) {
        if (active) {
          setError(err instanceof Error ? err.message : "Не удалось загрузить логи");
        }
      } finally {
        if (active) setRefreshing(false);
      }
    };

    void load();
    if (!pageVisible || livePaused) {
      return () => {
        active = false;
        controller?.abort();
      };
    }

    const timer = window.setInterval(() => {
      void load();
    }, 4000);

    return () => {
      active = false;
      controller?.abort();
      window.clearInterval(timer);
    };
  }, [params.id, pageVisible, livePaused]);

  const hasLogs = useMemo(() => {
    if (!logs) return false;
    return Boolean(
      logs.project_logs.trim() ||
        logs.deployment_logs.trim() ||
        logs.runtime_logs.trim() ||
        logs.runtime_error
    );
  }, [logs]);

  const canAnalyze = useMemo(() => {
    if (!logs) return false;
    return Boolean(
      logs.deployment_status === "failed" ||
        logs.runtime_error ||
        logs.runtime_logs.trim() ||
        (logs.deployment_logs.trim() && logs.deployment_status === "failed") ||
        logs.container_id
    );
  }, [logs]);

  const onAnalyzeRuntimeLogs = () => {
    if (!params.id || !logs) return;
    const excerpt = buildRepairLogExcerpt(logs);
    stashPendingRepair(params.id, excerpt);
    router.push(`/app/projects/${params.id}/chat?repair=1`);
  };

  if (logs === null && !error) return <PageLoader />;

  return (
    <div className="space-y-4">
      <Card hover={false} className="p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <span
              className={cn(
                "flex h-10 w-10 items-center justify-center rounded-[var(--ar-radius-sm)]",
                logs?.deployment_status === "completed"
                  ? "bg-emerald-50 text-emerald-600"
                  : "bg-sky-50 text-[var(--ar-sky)]"
              )}
            >
              {logs?.deployment_status === "completed" ? (
                <CheckCircle2 size={18} aria-hidden />
              ) : (
                <Activity size={18} aria-hidden />
              )}
            </span>
            <div>
              <p className="text-sm font-semibold text-[var(--ar-black)]">
                {logs?.deployment_status
                  ? `Деплой: ${deploymentStatusLabel(logs.deployment_status)}`
                  : "Логи проекта"}
              </p>
              <p className="text-xs text-[var(--ar-stone)]">
                {logs?.container_id
                  ? `Контейнер ${logs.container_id.slice(0, 12)}`
                  : livePaused || !pageVisible
                    ? "Обновление на паузе"
                    : "Автообновление каждые 4 с"}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setLivePaused((value) => !value)}
              className="inline-flex min-h-10 items-center gap-1.5 rounded-[var(--ar-radius-sm)] border border-black/10 bg-white px-3 text-xs font-medium text-[var(--ar-graphite)] hover:bg-black/[0.03]"
            >
              {livePaused ? <Play size={14} aria-hidden /> : <Pause size={14} aria-hidden />}
              {livePaused ? "Продолжить" : "Пауза"}
            </button>
            <div className="flex items-center gap-2 text-xs text-[var(--ar-stone)]">
              <RefreshCw size={14} className={cn(refreshing && "animate-spin")} aria-hidden />
              live
            </div>
          </div>
        </div>
      </Card>

      {error ? (
        <div className="flex items-start gap-2 rounded-[var(--ar-radius-md)] border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          <AlertCircle size={16} className="mt-0.5 shrink-0" aria-hidden />
          <span>{error}</span>
        </div>
      ) : null}

      {logs?.runtime_error ? (
        <div className="flex items-start gap-2 rounded-[var(--ar-radius-md)] border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <AlertCircle size={16} className="mt-0.5 shrink-0" aria-hidden />
          <span>{logs.runtime_error}</span>
        </div>
      ) : null}

      {canAnalyze ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-[var(--ar-radius-md)] border border-black/10 bg-white px-4 py-3">
          <p className="text-sm text-[var(--ar-mist)]">
            ИИ получит фрагмент логов, исправит код в чате и пересоберёт проект.
          </p>
          <Button variant="outline" size="sm" className="w-full sm:w-auto" onClick={onAnalyzeRuntimeLogs}>
            <ShieldCheck size={15} />
            Проверить и исправить в чате
          </Button>
        </div>
      ) : null}

      {hasLogs && logs ? (
        <div className="grid gap-4">
          {logs.project_logs.trim() ? (
            <LogBlock
              title="События платформы"
              hint="DNS, остановки, служебные заметки — не вывод контейнера"
              value={logs.project_logs}
              onExpand={() => setExpanded({ title: "События платформы", value: clipLog(logs.project_logs) })}
            />
          ) : null}
          {logs.deployment_logs.trim() ? (
            <LogBlock
              title="Деплой / сборка"
              hint="Статус последнего деплоя и полный текст ошибки сборки, если есть"
              value={logs.deployment_logs}
              onExpand={() => setExpanded({ title: "Деплой / сборка", value: clipLog(logs.deployment_logs) })}
            />
          ) : null}
          {logs.runtime_logs.trim() ? (
            <LogBlock
              title="Контейнер (runtime)"
              hint="Живой stdout/stderr работающего контейнера"
              value={logs.runtime_logs}
              onExpand={() => setExpanded({ title: "Контейнер (runtime)", value: clipLog(logs.runtime_logs) })}
            />
          ) : null}
        </div>
      ) : (
        <Card hover={false}>
          <EmptyState
            className="border-0 bg-transparent shadow-none"
            title="Логов пока нет"
            description="Лог сборки смотрите во вкладке «Деплои». Здесь — события платформы и runtime контейнера."
          />
        </Card>
      )}

      <Modal
        open={expanded !== null}
        onClose={() => setExpanded(null)}
        title={expanded ? `Логи: ${expanded.title}` : ""}
        className="max-w-4xl"
      >
        <pre className="max-h-[75vh] overflow-auto whitespace-pre-wrap scrollbar-airy bg-[#0d1117] p-4 font-mono text-xs leading-relaxed text-[#c9d1d9]">
          {expanded?.value}
        </pre>
      </Modal>
    </div>
  );
}
