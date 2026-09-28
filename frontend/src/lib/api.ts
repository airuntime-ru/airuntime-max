import { clearTokens, getAccessToken, getRefreshToken, setTokens } from "@/lib/auth";

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

type TokenResponse = {
  access_token: string;
  refresh_token: string;
};

export type ProjectType = {
  id: string;
  type: string;
  name: string;
  description: string;
  status: string;
  logs: string;
  deployment_url: string | null;
  deploy_subdomain: string | null;
  blocked_reason: string | null;
  planned_site_url: string | null;
  git_history: string;
};

export type DeploymentType = {
  id: string;
  project_id: string;
  status: string;
  image_ref: string | null;
  container_id: string | null;
  logs_ref: string | null;
  error_text?: string | null;
  log_text?: string | null;
  started_at: string | null;
  finished_at: string | null;
};

export type ProjectLogsType = {
  project_logs: string;
  deployment_logs: string;
  runtime_logs: string;
  runtime_error: string | null;
  deployment_status: string | null;
  container_id: string | null;
  logs_ref: string | null;
};

export type MeType = {
  id: string;
  email: string;
  is_verified: boolean;
  credits_balance: number;
  onboarding_completed: boolean;
};

export type ChatType = {
  id: string;
  project_id: string;
  title: string;
  created_at: string;
  updated_at: string;
};

export type MessageType = {
  id: string;
  role: string;
  content_markdown: string;
  created_at: string;
  attachments?: ChatFileType[];
};

export type ChatFileType = {
  id: string;
  project_id: string;
  chat_id: string;
  message_id: string | null;
  original_filename: string;
  content_type: string;
  size_bytes: number;
  download_url: string | null;
  created_at: string;
};

export type ProvidersType = {
  active: string;
  supported: string[];
  configured: Record<string, boolean>;
  auto_provider?: string;
  auto_model?: string;
  defaults?: Record<string, string>;
  top_models?: Record<string, string[]>;
  models?: Record<string, ModelOptionType[]>;
  credits_per_rub?: number;
};

export type ModelOptionType = {
  id: string;
  label: string;
  description: string;
  tier: "quality" | "balanced" | "economy" | "other";
  input_usd_per_million: number | null;
  cached_input_usd_per_million: number | null;
  output_usd_per_million: number | null;
  input_credits_per_million: number | null;
  output_credits_per_million: number | null;
};

export type ProjectRuntimeLimitsType = {
  running: number;
  max_running: number;
  total: number;
  /** null when neither the plan nor the global setting caps the project count. */
  max_total: number | null;
};

export type TelegramBotProfileType = {
  username: string;
  url: string;
  name: string;
  description: string;
  short_description: string;
};

async function rawRequest(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
  const token = getAccessToken();
  let response: Response;
  try {
    response = await fetch(`${apiBase}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init.headers ?? {}),
      },
    });
  } catch (err) {
    throw new Error(describeDisconnectedError(err));
  }
  if (response.status !== 401 || !retry) return response;

  const refreshed = await refreshSession();
  if (!refreshed) return response;
  return rawRequest(path, init, false);
}

/** Browser fetch failures look like "the user's Wi-Fi died". They are almost always the
 *  server/proxy dropping a long SSE stream, not the client's network. */
export function describeDisconnectedError(err: unknown): string {
  const message = err instanceof Error ? err.message : String(err || "");
  const lower = message.toLowerCase();
  if (
    lower === "failed to fetch" ||
    lower === "network error" ||
    lower === "load failed" ||
    lower === "networkerror when attempting to fetch resource." ||
    lower.includes("failed to fetch") ||
    lower.includes("networkerror")
  ) {
    return "Связь с агентом оборвалась на сервере. Это не ваша сеть — отправьте сообщение ещё раз.";
  }
  return message || "Не удалось получить ответ агента";
}

export type OutOfCreditsAction = { id: string; label: string; href: string };

/** Structured 402 body (backend services/credit_gate.py), so the UI can offer real choices. */
export type OutOfCreditsDetail = {
  code: "out_of_credits";
  message: string;
  balance: number;
  has_byok: boolean;
  pending_plan_request: boolean;
  actions: OutOfCreditsAction[];
};

/** Thrown instead of a plain Error so callers can render the paywall instead of a red string. */
export class OutOfCreditsError extends Error {
  readonly detail: OutOfCreditsDetail;

  constructor(detail: OutOfCreditsDetail) {
    super(detail.message);
    this.name = "OutOfCreditsError";
    this.detail = detail;
  }
}

function asOutOfCredits(detail: unknown): OutOfCreditsDetail | null {
  if (detail && typeof detail === "object" && (detail as { code?: string }).code === "out_of_credits") {
    return detail as OutOfCreditsDetail;
  }
  return null;
}

async function parseErrorMessage(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const payload = JSON.parse(text) as { detail?: unknown };
    const outOfCredits = asOutOfCredits(payload.detail);
    if (outOfCredits) throw new OutOfCreditsError(outOfCredits);
    if (typeof payload.detail === "string") return payload.detail;
    // FastAPI / Pydantic validation errors: detail is an array of {loc, msg, type}.
    if (Array.isArray(payload.detail)) {
      const parts = payload.detail.map((item) => {
        if (item && typeof item === "object" && "msg" in item) {
          const row = item as { loc?: unknown[]; msg?: string; type?: string };
          const field = Array.isArray(row.loc)
            ? row.loc.filter((part) => part !== "body").join(".")
            : "";
          const msg = row.msg || "Ошибка валидации";
          if (row.type === "string_too_long" || /at most \d+ characters/i.test(msg)) {
            return (
              "Сообщение слишком длинное. Вставьте более короткий фрагмент лога " +
              "или нажмите «Проверить и исправить» на странице логов."
            );
          }
          return field ? `${field}: ${msg}` : msg;
        }
        return String(item);
      });
      return parts.filter(Boolean).join("; ") || `Request failed: ${response.status}`;
    }
  } catch (err) {
    // The catch is here for "body is not JSON" - it must not swallow the typed paywall error.
    if (err instanceof OutOfCreditsError) throw err;
  }
  return text || `Request failed: ${response.status}`;
}

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await rawRequest(path, init);
  if (!response.ok) {
    throw new Error(await parseErrorMessage(response));
  }
  return (await response.json()) as T;
}

export async function requestAuthCode(email: string): Promise<void> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 25_000);
  try {
    await requestJson<{ message: string }>("/auth/request-code", {
      method: "POST",
      body: JSON.stringify({ email }),
      signal: controller.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error("Сервер долго не отвечает. Код мог уже уйти на почту — проверьте входящие.");
    }
    throw err;
  } finally {
    window.clearTimeout(timer);
  }
}

export async function verifyAuthCode(email: string, code: string): Promise<void> {
  const data = await requestJson<TokenResponse>("/auth/verify-code", {
    method: "POST",
    body: JSON.stringify({ email, code }),
  });
  setTokens(data.access_token, data.refresh_token);
}

/** @deprecated Password login is legacy; use requestAuthCode + verifyAuthCode */
export async function login(email: string, password: string): Promise<void> {
  const data = await requestJson<TokenResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  setTokens(data.access_token, data.refresh_token);
}

export async function register(email: string, password: string): Promise<void> {
  const data = await requestJson<TokenResponse>("/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  setTokens(data.access_token, data.refresh_token);
}

export async function refreshSession(): Promise<boolean> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return false;
  try {
    const data = await requestJson<TokenResponse>("/auth/refresh", {
      method: "POST",
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    setTokens(data.access_token, data.refresh_token);
    return true;
  } catch {
    clearTokens();
    return false;
  }
}

export async function logout(): Promise<void> {
  const refreshToken = getRefreshToken();
  // Clear locally first so UI can leave even if the revoke request hangs or fails.
  clearTokens();
  if (!refreshToken) return;
  try {
    await rawRequest("/auth/logout", { method: "POST", body: JSON.stringify({ refresh_token: refreshToken }) }, false);
  } catch {
    // Session is already gone on this device.
  }
}

export type PagedResult<T> = {
  items: T[];
  total: number;
};

export type ProjectListResult = PagedResult<ProjectType> & { deployed_total: number };

export async function listProjects(limit = 20, offset = 0): Promise<ProjectListResult> {
  return requestJson<ProjectListResult>(`/projects?limit=${limit}&offset=${offset}`);
}

export async function getProjectRuntimeLimits(): Promise<ProjectRuntimeLimitsType> {
  return requestJson<ProjectRuntimeLimitsType>("/projects/runtime-limits");
}

export async function stopProject(projectId: string): Promise<ProjectType> {
  return requestJson<ProjectType>(`/projects/${projectId}/stop`, { method: "POST" });
}

export async function startProject(projectId: string): Promise<ProjectType> {
  return requestJson<ProjectType>(`/projects/${projectId}/start`, { method: "POST" });
}

export async function getProject(projectId: string): Promise<ProjectType> {
  return requestJson<ProjectType>(`/projects/${projectId}`);
}

export type ProjectGenerationUsageType = {
  credits_spent: number;
  cost_rub: number;
  input_tokens: number;
  cached_input_tokens: number;
  cache_write_input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  generation_seconds: number;
  runs_count: number;
  charge_events: number;
};

export async function getProjectGenerationUsage(
  projectId: string
): Promise<ProjectGenerationUsageType> {
  return requestJson<ProjectGenerationUsageType>(`/projects/${projectId}/generation-usage`);
}

export async function deleteProject(projectId: string): Promise<void> {
  await requestJson(`/projects/${projectId}`, { method: "DELETE" });
}

export async function getProjectLogs(projectId: string): Promise<ProjectLogsType> {
  return requestJson<ProjectLogsType>(`/projects/${projectId}/logs`);
}

export async function createProject(payload: {
  type?: "telegram_bot" | "website" | "mixed";
  name: string;
  description?: string;
}): Promise<ProjectType> {
  return requestJson<ProjectType>("/projects", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateProject(
  projectId: string,
  payload: {
    name?: string;
    description?: string;
    deploy_subdomain?: string | null;
  }
): Promise<ProjectType> {
  return requestJson<ProjectType>(`/projects/${projectId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function createChat(projectId: string): Promise<ChatType> {
  return requestJson<ChatType>(`/projects/${projectId}/chats`, { method: "POST" });
}

export async function listChats(projectId: string): Promise<ChatType[]> {
  return requestJson<ChatType[]>(`/projects/${projectId}/chats`);
}

export async function listMessages(projectId: string, chatId: string): Promise<MessageType[]> {
  return requestJson<MessageType[]>(`/projects/${projectId}/chats/${chatId}/messages`);
}

export async function createMessage(
  projectId: string,
  chatId: string,
  content: string,
  attachmentIds: string[] = []
): Promise<void> {
  await requestJson(`/projects/${projectId}/chats/${chatId}/messages`, {
    method: "POST",
    body: JSON.stringify({ content, attachment_ids: attachmentIds }),
  });
}

export function streamChat(
  projectId: string,
  chatId: string,
  content: string,
  attachmentIds: string[] = [],
  options: { provider?: string | null; model?: string | null; signal?: AbortSignal } = {}
): Promise<Response> {
  return rawRequest(`/projects/${projectId}/chats/${chatId}/stream`, {
    method: "POST",
    body: JSON.stringify({
      content,
      attachment_ids: attachmentIds,
      provider: options.provider || undefined,
      model: options.model || undefined,
    }),
    signal: options.signal,
  });
}

export async function uploadChatFile(projectId: string, chatId: string, file: File): Promise<ChatFileType> {
  const token = getAccessToken();
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`${apiBase}/projects/${projectId}/chats/${chatId}/files`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Upload failed: ${response.status}`);
  }
  return (await response.json()) as ChatFileType;
}

export async function deleteChatFile(projectId: string, chatId: string, fileId: string): Promise<void> {
  await requestJson(`/projects/${projectId}/chats/${chatId}/files/${fileId}`, { method: "DELETE" });
}

export type SecretType = {
  id: string;
  key: string;
  reason: string | null;
  has_value: boolean;
  created_at: string;
};

export async function listSecrets(projectId: string): Promise<SecretType[]> {
  return requestJson<SecretType[]>(`/projects/${projectId}/secrets`);
}

export async function setSecretValue(
  projectId: string,
  secretId: string,
  value: string
): Promise<SecretType & { url?: string }> {
  return requestJson(`/projects/${projectId}/secrets/${secretId}`, {
    method: "PATCH",
    body: JSON.stringify({ value }),
  });
}

export async function getTelegramBotProfile(projectId: string): Promise<TelegramBotProfileType> {
  return requestJson<TelegramBotProfileType>(`/projects/${projectId}/telegram/profile`);
}

export async function updateTelegramBotProfile(
  projectId: string,
  payload: {
    name?: string;
    description?: string;
    short_description?: string;
  },
): Promise<TelegramBotProfileType> {
  return requestJson<TelegramBotProfileType>(`/projects/${projectId}/telegram/profile`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateTelegramBotAvatar(
  projectId: string,
  file: File,
): Promise<TelegramBotProfileType> {
  const token = getAccessToken();
  const form = new FormData();
  form.append("photo", file);
  const response = await fetch(`${apiBase}/projects/${projectId}/telegram/profile/photo`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Avatar upload failed: ${response.status}`);
  }
  return (await response.json()) as TelegramBotProfileType;
}

export async function listDeployments(
  projectId: string,
  limit = 20,
  offset = 0,
): Promise<PagedResult<DeploymentType>> {
  return requestJson<PagedResult<DeploymentType>>(
    `/projects/${projectId}/deployments?limit=${limit}&offset=${offset}`,
  );
}

export async function createDeployment(projectId: string): Promise<DeploymentType> {
  return requestJson<DeploymentType>(`/projects/${projectId}/deployments`, { method: "POST" });
}

export type DeploymentCheckResultType = {
  checked: boolean;
  found_errors: boolean;
  fixed: boolean;
  summary: string;
};

export async function checkDeployment(projectId: string): Promise<DeploymentCheckResultType> {
  return requestJson<DeploymentCheckResultType>(`/projects/${projectId}/check-deployment`, {
    method: "POST",
  });
}

export function streamRepairDeployment(
  projectId: string,
  chatId: string,
  options: { signal?: AbortSignal; errorLog?: string | null } = {}
): Promise<Response> {
  return rawRequest(`/projects/${projectId}/chats/${chatId}/repair-stream`, {
    method: "POST",
    body: JSON.stringify({
      error_log: options.errorLog?.trim() ? options.errorLog : undefined,
    }),
    signal: options.signal,
  });
}

export async function getMe(): Promise<MeType> {
  return requestJson<MeType>("/auth/me");
}

export async function completeOnboarding(): Promise<MeType> {
  return requestJson<MeType>("/auth/me/onboarding-complete", { method: "POST" });
}

export async function getProviders(): Promise<ProvidersType> {
  return requestJson<ProvidersType>("/providers");
}

export type ProjectVersionType = {
  commit_hash: string;
  created_at: string;
  message: string;
};

export async function listProjectVersions(projectId: string): Promise<ProjectVersionType[]> {
  return requestJson<ProjectVersionType[]>(`/projects/${projectId}/versions`);
}

export async function rollbackProjectVersion(
  projectId: string,
  commitHash: string,
): Promise<{ rollback_commit_hash: string; deployment: DeploymentType }> {
  return requestJson<{ rollback_commit_hash: string; deployment: DeploymentType }>(
    `/projects/${projectId}/versions/${commitHash}/rollback`,
    { method: "POST" },
  );
}

export async function downloadProjectVersionArchive(projectId: string, commitHash: string): Promise<Blob> {
  const token = getAccessToken();
  const response = await fetch(`${apiBase}/projects/${projectId}/versions/${commitHash}/archive`, {
    method: "GET",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Download failed: ${response.status}`);
  }
  return await response.blob();
}

export type ProjectVersionTreeEntryType = {
  name: string;
  entry_type: string;
  size_bytes?: number | null;
};

export async function listProjectVersionTree(
  projectId: string,
  commitHash: string,
  path: string,
): Promise<ProjectVersionTreeEntryType[]> {
  const token = getAccessToken();
  const url = new URL(
    `${apiBase}/projects/${projectId}/versions/${commitHash}/tree`,
  );
  url.searchParams.set("path", path);
  const response = await fetch(url.toString(), {
    method: "GET",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Tree load failed: ${response.status}`);
  }
  return (await response.json()) as ProjectVersionTreeEntryType[];
}

export type ProjectVersionFileType = {
  path: string;
  content: string;
  is_binary: boolean;
  truncated: boolean;
  size_bytes: number;
};

export async function getProjectVersionFile(
  projectId: string,
  commitHash: string,
  path: string,
): Promise<ProjectVersionFileType> {
  const token = getAccessToken();
  const url = new URL(
    `${apiBase}/projects/${projectId}/versions/${commitHash}/file`,
  );
  url.searchParams.set("path", path);
  const response = await fetch(url.toString(), {
    method: "GET",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `File load failed: ${response.status}`);
  }
  return (await response.json()) as ProjectVersionFileType;
}

export type PlanType = {
  id: string;
  key: string;
  name: string;
  description: string | null;
  /** Token budget included in the plan, in rubles - the number shown to users. */
  monthly_budget_rub: number;
  /** Derived from the budget; kept because the chat still meters in credits. */
  monthly_credits: number;
  max_concurrent_projects: number;
  max_projects: number;
  price_rub: number;
  /** false for the free tier: the grant is issued once at signup and never renews. */
  grant_renews: boolean;
  /** null = whole catalog; otherwise the only models allowed on the platform key. */
  allowed_models: string[] | null;
};

export type PlanChangeRequestType = {
  id: string;
  status: "pending" | "approved" | "rejected" | "cancelled";
  note: string | null;
  admin_note: string | null;
  created_at: string;
  resolved_at: string | null;
  to_plan_id: string;
  to_plan_name: string | null;
  from_plan_name: string | null;
};

export type BillingSummaryType = {
  credits_balance: number;
  balance_rub: number;
  billing_period_start: string | null;
  billing_period_end: string | null;
  plan: PlanType | null;
  pending_plan_request: PlanChangeRequestType | null;
  robokassa_enabled: boolean;
};

export type CreditTopUpType = {
  id: string;
  credits: number;
  amount_rub: number;
  status: "pending" | "paid" | "cancelled";
  created_at: string;
  paid_at: string | null;
  payment_url: string | null;
};

export async function listPlans(): Promise<PlanType[]> {
  return requestJson<PlanType[]>("/billing/plans");
}

export async function getBillingSummary(): Promise<BillingSummaryType> {
  return requestJson<BillingSummaryType>("/billing/me");
}

export async function createTopUp(credits: number): Promise<CreditTopUpType> {
  return requestJson<CreditTopUpType>("/billing/topups", {
    method: "POST",
    body: JSON.stringify({ credits }),
  });
}

export async function listTopUps(): Promise<CreditTopUpType[]> {
  return requestJson<CreditTopUpType[]>("/billing/topups");
}

/** Plans are never switched self-service - an admin approves the request after payment. */
export async function requestPlanChange(
  planId: string,
  note?: string,
): Promise<PlanChangeRequestType> {
  return requestJson<PlanChangeRequestType>("/billing/plan-requests", {
    method: "POST",
    body: JSON.stringify({ plan_id: planId, note: note || null }),
  });
}

export async function listPlanRequests(): Promise<PlanChangeRequestType[]> {
  return requestJson<PlanChangeRequestType[]>("/billing/plan-requests");
}

export async function cancelPlanRequest(requestId: string): Promise<PlanChangeRequestType> {
  return requestJson<PlanChangeRequestType>(`/billing/plan-requests/${requestId}/cancel`, {
    method: "POST",
  });
}

export type CreditLedgerEntryType = {
  id: string;
  amount: number;
  reason: "chat_message" | "topup" | "period_renewal" | "plan_change" | "signup_grant";
  project_id: string | null;
  project_name: string | null;
  provider: string | null;
  model: string | null;
  input_tokens: number | null;
  cached_input_tokens: number | null;
  cache_write_input_tokens: number | null;
  output_tokens: number | null;
  provider_cost_usd: number | null;
  cost_rub: number | null;
  created_at: string;
};

export type LedgerDirection = "all" | "credit" | "debit";

export type CreditLedgerPageType = PagedResult<CreditLedgerEntryType>;

export async function getUsageHistory(
  limit = 20,
  offset = 0,
  direction: LedgerDirection = "all",
): Promise<CreditLedgerPageType> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
    direction,
  });
  return requestJson<CreditLedgerPageType>(`/billing/usage?${params.toString()}`);
}

export type OrchestrationRunType = {
  id: string;
  project_id: string;
  chat_id: string;
  status: string;
  goal: string | null;
  original_request: string | null;
  complexity: string | null;
  plan_version: number;
  provider: string | null;
  model: string | null;
  credits_used: number;
  cost_rub: number;
  credit_budget: number | null;
  cancel_requested: boolean;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type OrchestrationTaskType = {
  id: string;
  local_id: string;
  title: string;
  role: string;
  execution_kind: string;
  status: string;
  attempt: number;
  max_attempts: number;
  sequence: number;
  workspace_mode: string;
  dependencies: string[];
  error_code: string | null;
  error_message: string | null;
};

export type OrchestrationRunDetailType = OrchestrationRunType & {
  tasks: OrchestrationTaskType[];
};

export async function listOrchestrationRuns(
  projectId: string,
  limit = 20,
  offset = 0,
): Promise<PagedResult<OrchestrationRunType>> {
  return requestJson<PagedResult<OrchestrationRunType>>(
    `/projects/${projectId}/orchestration/runs?limit=${limit}&offset=${offset}`,
  );
}

export async function getOrchestrationRun(
  projectId: string,
  runId: string,
): Promise<OrchestrationRunDetailType> {
  return requestJson<OrchestrationRunDetailType>(
    `/projects/${projectId}/orchestration/runs/${runId}`,
  );
}

export async function cancelOrchestrationRun(
  projectId: string,
  runId: string,
): Promise<OrchestrationRunType> {
  return requestJson<OrchestrationRunType>(
    `/projects/${projectId}/orchestration/runs/${runId}/cancel`,
    { method: "POST" },
  );
}

export async function resumeOrchestrationRun(
  projectId: string,
  runId: string,
): Promise<OrchestrationRunType> {
  return requestJson<OrchestrationRunType>(
    `/projects/${projectId}/orchestration/runs/${runId}/resume`,
    { method: "POST" },
  );
}

/** Raw Response for SSE consumption (events_bus wire format) - see lib/orchestration-stream.ts. */
export function streamOrchestrationRunEvents(
  projectId: string,
  runId: string,
  options: { afterSeq?: number; signal?: AbortSignal } = {},
): Promise<Response> {
  const params = new URLSearchParams();
  if (options.afterSeq) params.set("after_seq", String(options.afterSeq));
  const query = params.toString();
  return rawRequest(
    `/projects/${projectId}/orchestration/runs/${runId}/events${query ? `?${query}` : ""}`,
    { method: "GET", signal: options.signal },
  );
}

export type ByokCredentialType = {
  id: string;
  provider: string;
  last4: string;
  is_valid: boolean;
  validated_at: string | null;
  last_error: string | null;
  created_at: string;
};

export type ByokListType = {
  supported: string[];
  items: ByokCredentialType[];
};

export async function listByokCredentials(): Promise<ByokListType> {
  return requestJson<ByokListType>("/byok");
}

/** The key is write-only: responses only ever carry the last four characters. */
export async function saveByokCredential(
  provider: string,
  apiKey: string,
): Promise<ByokCredentialType> {
  return requestJson<ByokCredentialType>("/byok", {
    method: "PUT",
    body: JSON.stringify({ provider, api_key: apiKey }),
  });
}

export async function testByokCredential(provider: string): Promise<ByokCredentialType> {
  return requestJson<ByokCredentialType>(`/byok/${provider}/test`, { method: "POST" });
}

export async function deleteByokCredential(provider: string): Promise<void> {
  await requestJson<{ deleted: boolean }>(`/byok/${provider}`, { method: "DELETE" });
}

export type CustomDomainType = {
  custom_domain: string | null;
  status: "none" | "pending_dns" | "verified" | "error";
  verified_at: string | null;
  error: string | null;
  checked_at: string | null;
  target: { cname_target: string | null; a_record_ip: string | null };
};

export async function getCustomDomain(projectId: string): Promise<CustomDomainType> {
  return requestJson<CustomDomainType>(`/projects/${projectId}/domain`);
}

export async function setCustomDomain(
  projectId: string,
  domain: string | null,
): Promise<CustomDomainType> {
  return requestJson<CustomDomainType>(`/projects/${projectId}/domain`, {
    method: "PUT",
    body: JSON.stringify({ domain }),
  });
}

export async function verifyCustomDomain(projectId: string): Promise<CustomDomainType> {
  return requestJson<CustomDomainType>(`/projects/${projectId}/domain/verify`, { method: "POST" });
}

export type SupportMessageType = {
  id: string;
  conversation_id: string;
  sender_party: "user" | "staff";
  sender_user_id: string;
  body: string;
  read_at: string | null;
  created_at: string;
};

export type SupportConversationType = {
  id: string;
  status: string;
  messages: SupportMessageType[];
};

export type StaffConversationSummaryType = {
  id: string;
  user_id: string;
  user_email: string;
  credits_balance: number;
  status: string;
  unread_from_user: number;
  last_message_at: string | null;
  last_message_preview: string;
  last_sender_party: string | null;
};

export async function fetchSupportConversation(): Promise<SupportConversationType> {
  return requestJson<SupportConversationType>("/support/conversation");
}

export async function fetchSupportUnread(): Promise<{ unread: number }> {
  return requestJson<{ unread: number }>("/support/unread-count");
}

export async function sendSupportMessage(body: string): Promise<SupportMessageType> {
  return requestJson<SupportMessageType>("/support/messages", {
    method: "POST",
    body: JSON.stringify({ body }),
  });
}

export async function markSupportRead(messageIds: string[]): Promise<void> {
  await requestJson<{ updated: string[] }>("/support/read", {
    method: "POST",
    body: JSON.stringify({ message_ids: messageIds }),
  });
}

export async function fetchStaffConversations(options?: {
  status?: string;
  unreadOnly?: boolean;
  page?: number;
}): Promise<{ items: StaffConversationSummaryType[]; total: number }> {
  const params = new URLSearchParams();
  if (options?.status) params.set("status", options.status);
  if (options?.unreadOnly) params.set("unread_only", "true");
  if (options?.page) params.set("page", String(options.page));
  const qs = params.toString();
  return requestJson(`/support/staff/conversations${qs ? `?${qs}` : ""}`);
}

export async function fetchStaffConversation(conversationId: string): Promise<SupportConversationType> {
  return requestJson<SupportConversationType>(`/support/staff/conversations/${conversationId}/messages`);
}

export async function sendStaffMessage(
  conversationId: string,
  body: string,
): Promise<SupportMessageType> {
  return requestJson<SupportMessageType>(`/support/staff/conversations/${conversationId}/messages`, {
    method: "POST",
    body: JSON.stringify({ body }),
  });
}

export async function markStaffRead(conversationId: string, messageIds: string[]): Promise<void> {
  await requestJson(`/support/staff/conversations/${conversationId}/read`, {
    method: "POST",
    body: JSON.stringify({ message_ids: messageIds }),
  });
}

export async function closeStaffConversation(conversationId: string): Promise<void> {
  await requestJson(`/support/staff/conversations/${conversationId}/close`, { method: "POST" });
}

export async function openStaffConversationForUser(userId: string): Promise<SupportConversationType> {
  return requestJson<SupportConversationType>(
    `/support/staff/conversations/open-for-user/${userId}`,
    { method: "POST" },
  );
}

export { setAccessToken } from "@/lib/auth";
