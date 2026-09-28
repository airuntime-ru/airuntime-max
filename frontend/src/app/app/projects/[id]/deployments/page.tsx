"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Pause, Rocket, RotateCcw, ShieldCheck } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState, PageLoader } from "@/components/ui/loader";
import {
  createDeployment,
  getProject,
  getProjectRuntimeLimits,
  listDeployments,
  stopProject,
  type DeploymentType,
  type ProjectRuntimeLimitsType,
  type ProjectType,
} from "@/lib/api";
import {
  canCheckDeployment,
  canStopProject,
  deployActionLabel,
  deploymentStatusLabel,
  isProjectRunning,
  projectStatusLabel,
} from "@/lib/project-status";
import { stashPendingRepair } from "@/lib/chat-stream-runtime";
import { cn } from "@/lib/cn";

function statusTone(status: string) {
  if (status === "completed") return "text-emerald-700";
  if (status === "failed" || status === "cancelled") return "text-rose-700";
  if (status === "running") return "text-[var(--ar-sky)]";
  return "text-[var(--ar-stone)]";
}

function formatDateTime(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function deployLogBody(item: DeploymentType): string {
  const parts: string[] = [];
  if (item.log_text?.trim()) parts.push(item.log_text.trim());
  if (item.error_text?.trim()) {
    const err = item.error_text.trim();
    if (!item.log_text?.includes(err)) {
      parts.push(`--- Ошибка ---\n${err}`);
    }
  }
  return parts.join("\n\n");
}

function DeployLogPanel({ item, live }: { item: DeploymentType; live: boolean }) {
  const body = deployLogBody(item);
  if (!body) {
    return (
      <p className="text-xs text-[var(--ar-stone)]">
        {item.status === "queued" || item.status === "running"
          ? "Логи появятся, как только сборка начнётся…"
          : "Логов для этого деплоя нет."}
      </p>
    );
  }
  return (
    <div className="space-y-2">
      {live ? (
        <p className="text-[11px] font-medium uppercase tracking-wide text-[var(--ar-sky)]">
          Обновляется в реальном времени
        </p>
      ) : null}
      <pre className="max-h-[min(50vh,28rem)] overflow-auto whitespace-pre-wrap scrollbar-airy rounded-[0.7rem] border border-white/8 bg-[#0d1117] p-3 font-mono text-[11px] leading-relaxed text-[#c9d1d9]">
        {body}
      </pre>
    </div>
  );
}

const PAGE_SIZE = 20;

export default function ProjectDeploymentsPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const projectId = params.id;
  const [project, setProject] = useState<ProjectType | null>(null);
  const [limits, setLimits] = useState<ProjectRuntimeLimitsType | null>(null);
  const [deployments, setDeployments] = useState<DeploymentType[]>([]);
  const [deploymentsTotal, setDeploymentsTotal] = useState(0);
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set());
  const loadInFlightRef = useRef(false);

  const loadPage = useCallback(async (options?: { silent?: boolean; limit?: number }) => {
    if (!projectId) return;
    if (options?.silent && loadInFlightRef.current) return;
    loadInFlightRef.current = true;
    const limit = options?.limit ?? visibleCount;
    if (!options?.silent) setLoading(true);
    try {
      const [projectRow, deploymentResult, runtimeLimits] = await Promise.all([
        getProject(projectId),
        listDeployments(projectId, limit, 0),
        getProjectRuntimeLimits(),
      ]);
      setProject(projectRow);
      setDeployments(deploymentResult.items);
      setDeploymentsTotal(deploymentResult.total);
      setLimits(runtimeLimits);
      setError("");
      // Auto-expand the newest active deploy so live logs are visible without an extra click.
      const active = deploymentResult.items.find(
        (item) => item.status === "running" || item.status === "queued",
      );
      if (active) {
        setExpandedIds((prev) => {
          if (prev.has(active.id)) return prev;
          const next = new Set(prev);
          next.add(active.id);
          return next;
        });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить деплои");
    } finally {
      loadInFlightRef.current = false;
      if (!options?.silent) setLoading(false);
    }
  }, [projectId, visibleCount]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadPage();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadPage]);

  const onLoadMore = async () => {
    setLoadingMore(true);
    const nextLimit = visibleCount + PAGE_SIZE;
    try {
      await loadPage({ silent: true, limit: nextLimit });
      setVisibleCount(nextLimit);
    } finally {
      setLoadingMore(false);
    }
  };

  const hasActiveDeployments = deployments.some(
    (item) => item.status === "running" || item.status === "queued",
  );
  const hasExpandedActive = deployments.some(
    (item) =>
      expandedIds.has(item.id) && (item.status === "running" || item.status === "queued"),
  );

  useEffect(() => {
    // Poll faster when an expanded row is actively building so logs feel live.
    if (!hasActiveDeployments && !hasExpandedActive) return;
    if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
    const intervalMs = hasExpandedActive ? 2000 : 5000;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "hidden") return;
      void loadPage({ silent: true });
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [hasActiveDeployments, hasExpandedActive, loadPage]);

  const onDeploy = async () => {
    if (!projectId) return;
    setActionLoading(true);
    setError("");
    try {
      await createDeployment(projectId);
      await loadPage({ silent: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось поставить деплой в очередь");
    } finally {
      setActionLoading(false);
    }
  };

  const onCheckDeployment = () => {
    if (!projectId) return;
    const failed = deployments.find((item) => item.status === "failed");
    const excerpt = failed ? deployLogBody(failed) : "";
    stashPendingRepair(projectId, excerpt.slice(-100_000));
    router.push(`/app/projects/${projectId}/chat?repair=1`);
  };

  const onStop = async () => {
    if (!projectId) return;
    setActionLoading(true);
    setError("");
    try {
      setProject(await stopProject(projectId));
      setLimits(await getProjectRuntimeLimits());
      await loadPage({ silent: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось остановить проект");
    } finally {
      setActionLoading(false);
    }
  };

  const toggleExpanded = (id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  if (loading) return <PageLoader />;

  const atLimit = limits ? limits.running >= limits.max_running : false;
  // Redeploy of the currently running project is always allowed; starting another
  // while at the concurrent limit is not.
  const deployDisabled =
    actionLoading ||
    !project ||
    (atLimit && !isProjectRunning(project.status));
  const isLive = project?.status === "live";
  const showCheck = canCheckDeployment(deployments);
  const primaryLabel = project
    ? deployActionLabel(project.status, { loading: actionLoading })
    : "Собрать и запустить";

  return (
    <div className="space-y-4">
      {limits ? (
        <Card hover={false} className="border-[var(--ar-sky)]/20 bg-[var(--ar-sky)]/5">
          <p className="text-sm text-[var(--ar-mist)]">
            Запущено проектов:{" "}
            <span className="font-semibold text-[var(--ar-black)]">
              {limits.running} / {limits.max_running}
            </span>
            {project ? (
              <>
                {" "}
                · текущий статус: <Badge>{projectStatusLabel(project.status)}</Badge>
              </>
            ) : null}
          </p>
        </Card>
      ) : null}

      <div className="flex flex-col gap-3">
        <p className="text-sm leading-7 text-[var(--ar-mist)]">
          Каждый запуск заново собирает Docker-образ из текущего кода, затем поднимает контейнер.
          Разверните деплой, чтобы смотреть лог сборки в реальном времени.
        </p>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            {project && canStopProject(project.status) ? (
              <Button variant="outline" size="sm" disabled={actionLoading} onClick={onStop}>
                <Pause size={15} />
                Остановить
              </Button>
            ) : null}
            {showCheck ? (
              <Button variant="outline" size="sm" onClick={onCheckDeployment}>
                <ShieldCheck size={15} />
                Проверить и исправить
              </Button>
            ) : null}
          </div>
          {/* With no deploys yet the empty state already offers this exact action, centred and
              explained - two identical primary buttons in one viewport just adds noise. */}
          {deployments.length > 0 ? (
            <Button
              variant="accent"
              size="sm"
              disabled={deployDisabled}
              onClick={() => void onDeploy()}
            >
              {isLive ? <RotateCcw size={15} /> : <Rocket size={15} />}
              {primaryLabel}
            </Button>
          ) : null}
        </div>
      </div>
      {error ? <p className="text-sm text-rose-600">{error}</p> : null}
      {deployments.map((item) => {
        const expanded = expandedIds.has(item.id);
        const live = item.status === "running" || item.status === "queued";
        const hasLog = Boolean(deployLogBody(item) || live || item.status === "failed");
        return (
          <Card key={item.id} className="space-y-3" hover={false}>
            {/* items-start: without it the inline-flex Badge stretches to the full card width
                in the stacked mobile layout. */}
            <div className="flex flex-col items-start gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <p className="break-all font-semibold text-[var(--ar-black)]">
                  {item.image_ref ?? "Образ приложения"}
                </p>
                <p className="mt-1 text-xs text-[var(--ar-stone)]">ID: {item.id}</p>
              </div>
              <Badge className={statusTone(item.status)}>{deploymentStatusLabel(item.status)}</Badge>
            </div>
            {/* Only a run in progress needs a progress bar. A full-width saturated bar on every
                finished deploy shouted "something is wrong" on an otherwise healthy list. */}
            {item.status === "running" || item.status === "queued" ? (
              <div className="h-1 overflow-hidden rounded-full bg-black/[0.06]">
                <div
                  className={`beacon-bar h-full rounded-full ${
                    item.status === "running" ? "w-2/3 bg-sky-100" : "w-1/3 bg-black/[0.06]"
                  }`}
                />
              </div>
            ) : null}
            <div className="grid gap-1 text-xs text-[var(--ar-stone)] sm:grid-cols-2">
              <p>Старт: {formatDateTime(item.started_at)}</p>
              <p>Финиш: {formatDateTime(item.finished_at)}</p>
            </div>
            {hasLog ? (
              <div className="border-t border-black/5 pt-2">
                <button
                  type="button"
                  onClick={() => toggleExpanded(item.id)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-lg px-1 py-1.5 text-left text-sm font-medium text-[var(--ar-black)]",
                    "hover:bg-black/[0.03]",
                  )}
                >
                  {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                  {expanded ? "Свернуть лог" : "Развернуть лог"}
                  {item.status === "failed" && !expanded ? (
                    <span className="text-xs font-normal text-rose-600">— есть ошибка</span>
                  ) : null}
                  {live && !expanded ? (
                    <span className="text-xs font-normal text-[var(--ar-sky)]">— идёт сборка</span>
                  ) : null}
                </button>
                {expanded ? <DeployLogPanel item={item} live={live} /> : null}
              </div>
            ) : (
              <p className="text-xs text-[var(--ar-stone)]">Логи появятся после запуска</p>
            )}
          </Card>
        );
      })}
      {deployments.length === 0 ? (
        <EmptyState
          title="Деплоев пока нет"
          description="Запустите первую сборку, чтобы получить рабочий runtime."
          action={
            <Button variant="accent" disabled={deployDisabled} onClick={() => void onDeploy()}>
              <Rocket size={16} />
              Собрать и запустить
            </Button>
          }
        />
      ) : null}
      {deployments.length < deploymentsTotal ? (
        <div className="flex justify-center">
          <Button variant="outline" onClick={() => void onLoadMore()} disabled={loadingMore}>
            {loadingMore ? "Загружаем…" : "Показать ещё"}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
