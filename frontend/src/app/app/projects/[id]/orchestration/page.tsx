"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { GitBranch, Layers, RotateCcw, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState, Loader, PageLoader } from "@/components/ui/loader";
import {
  cancelOrchestrationRun,
  getOrchestrationRun,
  listOrchestrationRuns,
  resumeOrchestrationRun,
  type OrchestrationRunDetailType,
  type OrchestrationRunType,
  type OrchestrationTaskType,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { consumeOrchestrationEvents } from "@/lib/orchestration-stream";
import {
  isRunCancellable,
  isRunTerminal,
  roleLabel,
  runStatusLabel,
  runStatusTone,
  taskStatusLabel,
  taskStatusTone,
  workspaceModeLabel,
} from "@/lib/orchestration-status";

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Statuses where the engine is actively working on the task right now. */
function isTaskActive(status: string): boolean {
  return (
    status === "running" ||
    status === "collecting_evidence" ||
    status === "validating" ||
    status === "repairing"
  );
}

/** Node colour on the timeline rail, matching taskStatusTone's semantics. */
function taskNodeTone(status: string): string {
  if (status === "completed") return "border-emerald-300 bg-emerald-50 text-emerald-700";
  if (status === "failed" || status === "cancelled") return "border-rose-300 bg-rose-50 text-rose-700";
  if (status === "running" || status === "collecting_evidence" || status === "validating") {
    return "border-sky-300 bg-sky-50 text-sky-700";
  }
  if (status === "repairing" || status === "waiting_for_user") {
    return "border-amber-300 bg-amber-50 text-amber-700";
  }
  return "border-black/10 bg-white text-[var(--ar-stone)]";
}

/**
 * Rendered as a timeline rather than a stack of cards: the tasks are an ordered pipeline with
 * dependencies, and a flat list threw that away.
 */
function TaskRow({
  task,
  index,
  isLast,
  titleByLocalId,
}: {
  task: OrchestrationTaskType;
  index: number;
  isLast: boolean;
  titleByLocalId: Map<string, string>;
}) {
  const meta = [
    roleLabel(task.role),
    `попытка ${task.attempt}/${task.max_attempts}`,
    workspaceModeLabel(task.workspace_mode),
  ].filter(Boolean);

  return (
    <li className="relative flex gap-4 pb-5 last:pb-0">
      {!isLast ? (
        <span
          className="absolute bottom-0 left-[0.9375rem] top-8 w-px bg-black/[0.09]"
          aria-hidden
        />
      ) : null}
      <span
        className={cn(
          "relative z-10 flex h-[1.875rem] w-[1.875rem] shrink-0 items-center justify-center rounded-full border text-xs font-semibold",
          taskNodeTone(task.status),
          // A halo marks where the engine is right now; colour alone made "выполняется" and
          // "в очереди" look like the same kind of thing.
          isTaskActive(task.status) && "live-dot"
        )}
        aria-hidden
      >
        {index + 1}
      </span>

      <div className="min-w-0 flex-1 pt-0.5">
        <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1.5">
          <p className="min-w-0 font-medium text-[var(--ar-black)]">{task.title}</p>
          <Badge className={taskStatusTone(task.status)}>{taskStatusLabel(task.status)}</Badge>
        </div>
        <p className="mt-1 text-xs text-[var(--ar-stone)]">{meta.join(" · ")}</p>
        {task.dependencies.length > 0 ? (
          <p className="mt-1 text-xs text-[var(--ar-stone)]">
            После:{" "}
            {task.dependencies.map((id, i) => (
              <span key={id}>
                {i > 0 ? ", " : ""}
                <span className="text-[var(--ar-graphite)]">{titleByLocalId.get(id) ?? id}</span>
              </span>
            ))}
          </p>
        ) : null}
        {task.error_message ? (
          <p className="mt-2 rounded-[0.6rem] border border-rose-100 bg-rose-50/70 p-2 text-xs text-rose-700">
            {task.error_message}
          </p>
        ) : null}
      </div>
    </li>
  );
}

function RunDetailView({
  projectId,
  runId,
  onChanged,
}: {
  projectId: string;
  runId: string;
  onChanged: () => void;
}) {
  const [detail, setDetail] = useState<OrchestrationRunDetailType | null>(null);
  const [error, setError] = useState("");
  const [actionLoading, setActionLoading] = useState(false);
  const refetchTimer = useRef<number | null>(null);
  const refetchInFlight = useRef(false);

  const load = useCallback(async () => {
    refetchInFlight.current = true;
    try {
      const row = await getOrchestrationRun(projectId, runId);
      setDetail(row);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить запуск");
    } finally {
      refetchInFlight.current = false;
    }
  }, [projectId, runId]);

  const scheduleLoad = useCallback(() => {
    if (refetchInFlight.current) {
      // Coalesce bursts of SSE events into a single trailing refetch instead of a flood.
      if (refetchTimer.current) window.clearTimeout(refetchTimer.current);
      refetchTimer.current = window.setTimeout(() => void load(), 300);
      return;
    }
    void load();
  }, [load]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDetail(null);
      void load();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  useEffect(() => {
    if (!detail || isRunTerminal(detail.status)) return undefined;
    const controller = new AbortController();
    void consumeOrchestrationEvents(projectId, runId, {
      signal: controller.signal,
      onEvent: scheduleLoad,
      onError: () => {
        // Live tail dropped (network hiccup, reload, etc.) - the page keeps showing the last
        // known state; a manual refresh or reopening the page re-establishes it.
      },
    });
    return () => controller.abort();
    // Only (re)opens when the run transitions in/out of a terminal status - not on every detail
    // update, which would tear down and reopen the stream on every single event.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, runId, detail?.status === undefined || isRunTerminal(detail?.status ?? "")]);

  const titleByLocalId = useMemo(() => {
    const map = new Map<string, string>();
    for (const task of detail?.tasks ?? []) map.set(task.local_id, task.title);
    return map;
  }, [detail?.tasks]);

  const onCancel = async () => {
    setActionLoading(true);
    try {
      await cancelOrchestrationRun(projectId, runId);
      await load();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отменить запуск");
    } finally {
      setActionLoading(false);
    }
  };

  const onResume = async () => {
    setActionLoading(true);
    try {
      await resumeOrchestrationRun(projectId, runId);
      await load();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось возобновить запуск");
    } finally {
      setActionLoading(false);
    }
  };

  if (!detail) return <Loader label="Загружаем запуск…" />;

  const sortedTasks = [...detail.tasks].sort((a, b) => a.sequence - b.sequence);

  return (
    <div className="space-y-4">
      <Card hover={false} className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="break-words text-sm font-medium text-[var(--ar-black)]">
              {detail.goal || detail.original_request || "Без описания"}
            </p>
            <p className="mt-1 text-xs text-[var(--ar-stone)]">
              План v{detail.plan_version} · создан {formatDateTime(detail.created_at)}
            </p>
          </div>
          <Badge className={runStatusTone(detail.status)}>{runStatusLabel(detail.status)}</Badge>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--ar-stone)]">
          <span>
            Потрачено: {detail.credits_used.toLocaleString("ru-RU")} кредитов ·{" "}
            {detail.cost_rub.toLocaleString("ru-RU", {
              minimumFractionDigits: 2,
              maximumFractionDigits: 2,
            })}{" "}
            ₽
            {detail.credit_budget ? ` из ${detail.credit_budget}` : ""}
          </span>
          {detail.model ? (
            <span>
              Модель: {detail.model}
              {detail.provider ? ` · ${detail.provider}` : ""}
            </span>
          ) : null}
          {detail.error_code === "budget_exceeded" ? (
            <span className="text-amber-700">
              Бюджет исчерпан — пополните баланс и нажмите «Продолжить»
            </span>
          ) : detail.error_message ? (
            <span className="text-rose-600">{detail.error_message}</span>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-2 pt-1">
          {detail.status === "waiting_for_user" ? (
            <Button variant="accent" size="sm" disabled={actionLoading} onClick={() => void onResume()}>
              <RotateCcw size={14} />
              Продолжить
            </Button>
          ) : null}
          {isRunCancellable(detail.status) && !detail.cancel_requested ? (
            <Button variant="outline" size="sm" disabled={actionLoading} onClick={() => void onCancel()}>
              <XCircle size={14} />
              Остановить
            </Button>
          ) : null}
          {detail.cancel_requested && !isRunTerminal(detail.status) ? (
            <span className="inline-flex items-center text-xs text-[var(--ar-stone)]">
              Остановка запрошена…
            </span>
          ) : null}
        </div>
      </Card>

      {error ? <p className="text-sm text-rose-600">{error}</p> : null}

      {sortedTasks.length > 0 ? (
        <Card hover={false} className="p-5 sm:p-6">
          <ol className="relative">
            {sortedTasks.map((task, index) => (
              <TaskRow
                key={task.id}
                task={task}
                index={index}
                isLast={index === sortedTasks.length - 1}
                titleByLocalId={titleByLocalId}
              />
            ))}
          </ol>
        </Card>
      ) : (
        <p className="text-sm text-[var(--ar-stone)]">План ещё строится…</p>
      )}
    </div>
  );
}

export default function ProjectOrchestrationPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [runs, setRuns] = useState<OrchestrationRunType[] | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [loadError, setLoadError] = useState("");

  const loadRuns = useCallback(async () => {
    if (!projectId) return;
    try {
      const result = await listOrchestrationRuns(projectId, 20, 0);
      setRuns(result.items);
      setLoadError("");
      setSelectedRunId((current) => current ?? result.items[0]?.id ?? null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Не удалось загрузить запускs");
      setRuns([]);
    }
  }, [projectId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadRuns(), 0);
    return () => window.clearTimeout(timer);
  }, [loadRuns]);

  if (runs === null) return <PageLoader />;

  return (
    <div className="space-y-4">
      <p className="text-sm leading-7 text-[var(--ar-mist)]">
        Здесь видно, как оркестрационный движок разбивает запрос на задачи, кто их выполняет и в
        каком порядке. Новый запуск появляется здесь на каждое сообщение в чате.
      </p>
      {loadError ? <p className="text-sm text-rose-600">{loadError}</p> : null}

      {runs.length > 1 ? (
        <div className="flex flex-wrap items-center gap-2">
          {runs.map((run) => (
            <button
              key={run.id}
              type="button"
              onClick={() => setSelectedRunId(run.id)}
              className={cn(
                "inline-flex min-h-9 items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors",
                run.id === selectedRunId
                  ? "border-[var(--ar-sky)]/40 bg-[var(--ar-sky)]/10 text-[var(--ar-black)]"
                  : "border-black/10 bg-white text-[var(--ar-stone)] hover:border-black/20",
              )}
            >
              <GitBranch size={13} aria-hidden />
              {formatDateTime(run.created_at)}
            </button>
          ))}
        </div>
      ) : null}

      {runs.length === 0 ? (
        <EmptyState
          title="Запусков оркестрации пока нет"
          description="Начните новый запрос в чате — здесь появится план и ход его выполнения."
          action={
            <span className="inline-flex items-center gap-2 text-sm text-[var(--ar-stone)]">
              <Layers size={16} aria-hidden />
              Список обновится автоматически
            </span>
          }
        />
      ) : selectedRunId ? (
        <RunDetailView projectId={projectId} runId={selectedRunId} onChanged={() => void loadRuns()} />
      ) : null}
    </div>
  );
}
