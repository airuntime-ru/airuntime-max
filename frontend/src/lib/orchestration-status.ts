const RUN_STATUS_LABELS: Record<string, string> = {
  created: "Создан",
  analyzing: "Анализирует запрос",
  planning: "Строит план",
  running: "Выполняется",
  replanning: "Перестраивает план",
  waiting_for_user: "Ждёт вас",
  validating: "Проверяет результат",
  integrating: "Объединяет изменения",
  building: "Собирает проект",
  deploying: "Разворачивает",
  verifying_runtime: "Проверяет запуск",
  completed: "Готово",
  failed: "Ошибка",
  cancelled: "Отменено",
};

export function runStatusLabel(status: string): string {
  return RUN_STATUS_LABELS[status] ?? status;
}

const RUN_TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled"]);

export function isRunTerminal(status: string): boolean {
  return RUN_TERMINAL_STATUSES.has(status);
}

export function isRunCancellable(status: string): boolean {
  return !isRunTerminal(status);
}

const TASK_STATUS_LABELS: Record<string, string> = {
  pending: "В очереди",
  blocked: "Ждёт зависимости",
  ready: "Готова к запуску",
  running: "Выполняется",
  collecting_evidence: "Собирает доказательства",
  validating: "Проверяется",
  repairing: "Исправляется",
  waiting_for_user: "Ждёт вас",
  completed: "Готова",
  failed: "Ошибка",
  skipped: "Пропущена",
  cancelled: "Отменена",
};

export function taskStatusLabel(status: string): string {
  return TASK_STATUS_LABELS[status] ?? status;
}

/** Tailwind text/bg classes for a status Badge, matching the semantic colors used for
 * deployments/projects elsewhere (emerald=done, rose=failed, sky=active, stone=idle). */
export function taskStatusTone(status: string): string {
  if (status === "completed") return "border-emerald-200 bg-emerald-50 text-emerald-800";
  if (status === "failed" || status === "cancelled") return "border-rose-200 bg-rose-50 text-rose-700";
  if (status === "skipped") return "border-black/10 bg-black/[0.03] text-[var(--ar-mist)]";
  if (status === "running" || status === "collecting_evidence" || status === "validating") {
    return "border-sky-200 bg-sky-50 text-sky-800";
  }
  if (status === "repairing") return "border-amber-200 bg-amber-50 text-amber-800";
  if (status === "waiting_for_user") return "border-amber-200 bg-amber-50 text-amber-800";
  return "border-black/10 bg-black/[0.03] text-[var(--ar-mist)]";
}

export function runStatusTone(status: string): string {
  if (status === "completed") return "border-emerald-200 bg-emerald-50 text-emerald-800";
  if (status === "failed" || status === "cancelled") return "border-rose-200 bg-rose-50 text-rose-700";
  if (status === "waiting_for_user") return "border-amber-200 bg-amber-50 text-amber-800";
  if (status === "created") return "border-black/10 bg-black/[0.03] text-[var(--ar-mist)]";
  return "border-sky-200 bg-sky-50 text-sky-800";
}

const ROLE_LABELS: Record<string, string> = {
  product_planner: "Продуктовый план",
  solution_architect: "Архитектура",
  implementer: "Разработка",
  ui_ux_specialist: "UI/UX",
  build_fixer: "Исправление сборки",
  deploy_fixer: "Исправление запуска",
  qa_reviewer: "QA-проверка",
  security_reviewer: "Проверка безопасности",
  integration_agent: "Объединение веток",
};

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

const WORKSPACE_MODE_LABELS: Record<string, string> = {
  shared_sequential: "по очереди",
  parallel_read_only: "параллельно, только чтение",
  isolated_worktree: "в изолированной ветке",
};

export function workspaceModeLabel(mode: string): string {
  return WORKSPACE_MODE_LABELS[mode] ?? mode;
}
