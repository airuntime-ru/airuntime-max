const STATUS_LABELS: Record<string, string> = {
  created: "Создан",
  ready: "Готов к запуску",
  deploying: "Запускается",
  live: "Запущен",
  stopped: "Остановлен",
  needs_configuration: "Нужна настройка",
  telegram_ready: "Бот настроен",
  blocked: "Заблокирован модерацией",
};

export function projectStatusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

const TYPE_LABELS: Record<string, string> = {
  website: "Сайт",
  telegram_bot: "Telegram-бот",
  mixed: "Сайт и бот",
};

/** Shared so no screen ever leaks the raw enum ("website") to the user. */
export function projectTypeLabel(type: string): string {
  return TYPE_LABELS[type] ?? type;
}

export type ProjectStatusTone = "live" | "progress" | "warn" | "danger" | "idle";

/** Semantic colour bucket for a status, so every surface tints it the same way. */
export function projectStatusTone(status: string): ProjectStatusTone {
  if (status === "live") return "live";
  if (status === "deploying") return "progress";
  if (status === "needs_configuration") return "warn";
  if (status === "blocked") return "danger";
  return "idle";
}

const DEPLOYMENT_STATUS_LABELS: Record<string, string> = {
  completed: "Развёрнут",
  failed: "Ошибка",
  cancelled: "Отменён",
  running: "Запускается",
  queued: "В очереди",
};

export function deploymentStatusLabel(status: string): string {
  return DEPLOYMENT_STATUS_LABELS[status] ?? status;
}

export function isProjectRunning(status: string): boolean {
  return status === "live" || status === "deploying";
}

export function canStopProject(status: string): boolean {
  return isProjectRunning(status);
}

export function canStartProject(status: string): boolean {
  return (
    status === "created" ||
    status === "stopped" ||
    status === "ready" ||
    status === "needs_configuration" ||
    status === "telegram_ready"
  );
}

/** Primary deploy CTA: restart when already live/deploying, otherwise build & start. */
export function deployActionLabel(
  projectStatus: string,
  { loading = false }: { loading?: boolean } = {}
): string {
  if (loading) {
    return isProjectRunning(projectStatus) ? "Перезапускаем…" : "Запускаем…";
  }
  return projectStatus === "live" || projectStatus === "deploying"
    ? "Перезапустить"
    : "Собрать и запустить";
}

/**
 * "Проверить и исправить" only when the latest finished deployment failed.
 * Live/healthy projects should not show a repair CTA.
 */
export function canCheckDeployment(deployments: { status: string }[]): boolean {
  const latestFinished = deployments.find(
    (item) => item.status === "failed" || item.status === "completed" || item.status === "cancelled"
  );
  return latestFinished?.status === "failed";
}
