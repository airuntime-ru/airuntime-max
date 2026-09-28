"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ExternalLink, MessageSquare, Rocket, Settings, Square } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ProjectStatusChip } from "@/components/app/project-status-chip";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { PageLoader } from "@/components/ui/loader";
import {
  createDeployment,
  getProject,
  getProjectGenerationUsage,
  getProjectRuntimeLimits,
  stopProject,
  type ProjectGenerationUsageType,
  type ProjectRuntimeLimitsType,
  type ProjectType,
} from "@/lib/api";
import {
  canStartProject,
  canStopProject,
  isProjectRunning,
  projectTypeLabel,
} from "@/lib/project-status";

function formatRub(value: number): string {
  return value.toLocaleString("ru-RU", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function formatTokens(value: number): string {
  return value.toLocaleString("ru-RU");
}

function formatGenerationDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.round(totalSeconds));
  if (seconds < 60) return `${seconds} с`;
  const minutes = Math.floor(seconds / 60);
  const remSeconds = seconds % 60;
  if (minutes < 60) {
    return remSeconds > 0 ? `${minutes} мин ${remSeconds} с` : `${minutes} мин`;
  }
  const hours = Math.floor(minutes / 60);
  const remMinutes = minutes % 60;
  if (remMinutes === 0) return `${hours} ч`;
  return `${hours} ч ${remMinutes} мин`;
}

function pluralizeRuns(count: number): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return "запуск";
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return "запуска";
  return "запусков";
}

export default function ProjectOverviewPage() {
  const params = useParams<{ id: string }>();
  const [project, setProject] = useState<ProjectType | null>(null);
  const [limits, setLimits] = useState<ProjectRuntimeLimitsType | null>(null);
  const [usage, setUsage] = useState<ProjectGenerationUsageType | null>(null);
  const [error, setError] = useState("");
  const [actionLoading, setActionLoading] = useState(false);

  const loadProject = useCallback(async () => {
    if (!params.id) return;
    const [row, runtimeLimits] = await Promise.all([
      getProject(params.id),
      getProjectRuntimeLimits(),
    ]);
    setProject(row);
    setLimits(runtimeLimits);
    setError("");
    try {
      setUsage(await getProjectGenerationUsage(params.id));
    } catch {
      setUsage(null);
    }
  }, [params.id]);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        await loadProject();
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : "Не удалось загрузить проект");
      }
    })();
    return () => {
      active = false;
    };
  }, [loadProject]);

  const onStop = async () => {
    if (!project) return;
    setActionLoading(true);
    setError("");
    try {
      setProject(await stopProject(project.id));
      setLimits(await getProjectRuntimeLimits());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось остановить проект");
    } finally {
      setActionLoading(false);
    }
  };

  const onStart = async () => {
    if (!project) return;
    setActionLoading(true);
    setError("");
    try {
      await createDeployment(project.id);
      await loadProject();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось запустить проект");
    } finally {
      setActionLoading(false);
    }
  };

  if (!project) return <PageLoader />;

  const atLimit = limits ? limits.running >= limits.max_running : false;
  const startDisabled = actionLoading || (atLimit && !isProjectRunning(project.status));
  const chatHref = `/app/projects/${project.id}/chat`;
  const isLive = project.status === "live";
  const isDeploying = project.status === "deploying";
  const needsConfig = project.status === "needs_configuration";
  const canRun = canStartProject(project.status) && !needsConfig;

  return (
    <div className="space-y-4">
      {error ? <p className="text-sm text-rose-600">{error}</p> : null}

      {/* One divided card instead of three stacked ones: on mobile three facts this small
          should not cost three card-heights of scrolling. */}
      <Card hover={false} className="overflow-hidden p-0">
        <dl className="grid divide-y divide-black/[0.06] sm:grid-cols-[auto_auto_1fr] sm:divide-x sm:divide-y-0">
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 p-4 sm:block sm:p-5">
            <dt className="text-[0.68rem] font-semibold uppercase tracking-[0.14em] text-[var(--ar-stone)]">
              Статус
            </dt>
            <dd className="flex flex-wrap items-center gap-2 sm:mt-2.5">
              <ProjectStatusChip status={project.status} />
              {canStopProject(project.status) ? (
                <Button variant="ghost" size="sm" disabled={actionLoading} onClick={onStop}>
                  <Square size={11} fill="currentColor" />
                  Стоп
                </Button>
              ) : null}
            </dd>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 p-4 sm:block sm:p-5">
            <dt className="text-[0.68rem] font-semibold uppercase tracking-[0.14em] text-[var(--ar-stone)]">
              Тип
            </dt>
            <dd className="text-sm font-medium text-[var(--ar-black)] sm:mt-2.5">
              {projectTypeLabel(project.type)}
            </dd>
          </div>

          <div className="min-w-0 p-4 sm:p-5">
            <dt className="text-[0.68rem] font-semibold uppercase tracking-[0.14em] text-[var(--ar-stone)]">
              Публичная ссылка
            </dt>
            <dd className="mt-2 sm:mt-2.5">
              {project.deployment_url ? (
                <a
                  href={project.deployment_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 break-all font-mono text-sm font-medium text-[var(--ar-sky)] hover:underline"
                >
                  {project.deployment_url}
                  <ExternalLink size={14} className="shrink-0" aria-hidden />
                </a>
              ) : (
                <p className="text-sm text-[var(--ar-mist)]">Появится после первого запуска</p>
              )}
            </dd>
          </div>
        </dl>
      </Card>

      {usage ? (
        <Card hover={false} className="overflow-hidden p-0">
          <div className="border-b border-black/[0.06] px-4 py-3 sm:px-5">
            <p className="text-[0.68rem] font-semibold uppercase tracking-[0.14em] text-[var(--ar-stone)]">
              Генерации по проекту
            </p>
            <p className="mt-1 text-sm text-[var(--ar-mist)]">
              Суммарно по всем запускам агента в этом проекте
            </p>
          </div>
          <dl className="grid divide-y divide-black/[0.06] sm:grid-cols-3 sm:divide-x sm:divide-y-0">
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 p-4 sm:block sm:p-5">
              <dt className="text-[0.68rem] font-semibold uppercase tracking-[0.14em] text-[var(--ar-stone)]">
                Токены
              </dt>
              <dd className="tabular-nums text-sm font-semibold text-[var(--ar-black)] sm:mt-2.5 sm:text-base">
                {formatTokens(usage.total_tokens)}
              </dd>
              <p className="mt-1 w-full text-xs text-[var(--ar-mist)] sm:w-auto">
                {formatTokens(usage.input_tokens)} вход · {formatTokens(usage.output_tokens)} выход
              </p>
            </div>
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 p-4 sm:block sm:p-5">
              <dt className="text-[0.68rem] font-semibold uppercase tracking-[0.14em] text-[var(--ar-stone)]">
                Стоимость
              </dt>
              <dd className="tabular-nums text-sm font-semibold text-[var(--ar-black)] sm:mt-2.5 sm:text-base">
                {formatRub(usage.cost_rub)} ₽
              </dd>
              <p className="mt-1 w-full text-xs text-[var(--ar-mist)] sm:w-auto">
                {usage.credits_spent.toLocaleString("ru-RU")} кредитов
              </p>
            </div>
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 p-4 sm:block sm:p-5">
              <dt className="text-[0.68rem] font-semibold uppercase tracking-[0.14em] text-[var(--ar-stone)]">
                Время
              </dt>
              <dd className="tabular-nums text-sm font-semibold text-[var(--ar-black)] sm:mt-2.5 sm:text-base">
                {formatGenerationDuration(usage.generation_seconds)}
              </dd>
              <p className="mt-1 w-full text-xs text-[var(--ar-mist)] sm:w-auto">
                {usage.runs_count.toLocaleString("ru-RU")} {pluralizeRuns(usage.runs_count)}
              </p>
            </div>
          </dl>
        </Card>
      ) : null}

      {limits && atLimit && !isProjectRunning(project.status) ? (
        <Card hover={false} className="border-amber-200 bg-amber-50 p-4">
          <p className="text-sm text-amber-900">
            Лимит одновременных проектов: {limits.running}/{limits.max_running}. Остановите другой проект,
            чтобы запустить этот.
          </p>
        </Card>
      ) : null}

      <Card hover={false} className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-[var(--ar-black)]">
            {isLive
              ? "Проект запущен"
              : isDeploying
                ? "Идёт сборка и запуск"
                : needsConfig
                  ? "Нужна настройка"
                  : canRun
                    ? "Готов к запуску"
                    : "Продолжите в чате"}
          </p>
          <p className="mt-1 text-sm text-[var(--ar-mist)]">
            {isLive
              ? "Правки вносите через чат — платформа пересоберёт проект."
              : isDeploying
                ? "Статус обновится сам. Подробности — во вкладках «Деплои» и «Логи»."
                : needsConfig
                  ? "Заполните секреты в настройках — запуск продолжится автоматически."
                  : canRun
                    ? "Сборка создаст образ и поднимет контейнер."
                    : "Опишите задачу в чате, чтобы собрать первую версию."}
          </p>
        </div>
        <div className="flex w-full flex-col gap-2 sm:w-auto sm:min-w-[14rem]">
          {isLive && project.deployment_url ? (
            <a href={project.deployment_url} target="_blank" rel="noreferrer">
              <Button variant="accent" className="w-full">
                <ExternalLink size={16} />
                Открыть сайт
              </Button>
            </a>
          ) : null}
          {isDeploying ? (
            <Link href={`/app/projects/${project.id}/deployments`}>
              <Button variant="accent" className="w-full">
                Смотреть деплои
              </Button>
            </Link>
          ) : null}
          {needsConfig ? (
            <Link href={`/app/projects/${project.id}/settings`}>
              <Button variant="accent" className="w-full">
                <Settings size={16} />
                Настройки
              </Button>
            </Link>
          ) : null}
          {canRun ? (
            <Button variant="accent" className="w-full" disabled={startDisabled} onClick={() => void onStart()}>
              <Rocket size={16} />
              {actionLoading ? "Запускаем…" : "Собрать и запустить"}
            </Button>
          ) : null}
          <Link href={chatHref}>
            <Button variant="outline" className="w-full">
              <MessageSquare size={16} />
              Открыть чат
            </Button>
          </Link>
        </div>
      </Card>
    </div>
  );
}
