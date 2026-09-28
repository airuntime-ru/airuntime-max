import { hasAnalyticsConsent } from "@/lib/consent";

export type AnalyticsPlatform = "web" | "telegram" | "ios" | "android";

type AnalyticsEventName = "session_start" | "screen_view" | "screen_leave";

type QueuedEvent = {
  name: AnalyticsEventName;
  screen?: string;
  tab?: string;
  duration_ms?: number;
  ts?: string;
  props?: Record<string, string | number | boolean | null>;
};

type BatchPayload = {
  session_id: string;
  anonymous_id: string;
  platform: AnalyticsPlatform;
  user_id?: string;
  entry_screen?: string;
  referrer?: string;
  utm_source?: string;
  utm_medium?: string;
  utm_campaign?: string;
  events: QueuedEvent[];
};

const ANON_KEY = "airuntime-analytics-anon-id";
const SESSION_KEY = "airuntime-analytics-session-id";
const FLUSH_MS = 5_000;
const FLUSH_DEBOUNCE_MS = 800;
const MAX_BATCH = 40;

const API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").replace(/\/$/, "");
const INGEST_KEY = (process.env.NEXT_PUBLIC_ANALYTICS_INGEST_KEY ?? "").trim();

let platform: AnalyticsPlatform = "web";
let userId: string | null = null;
let sessionStarted = false;
let initialized = false;
const queue: QueuedEvent[] = [];
let intervalTimer: ReturnType<typeof setInterval> | null = null;
let debounceTimer: ReturnType<typeof setTimeout> | null = null;
let currentScreen: string | null = null;
let currentScreenStartedAt = 0;

function storageGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function storageSet(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* ignore */
  }
}

function sessionGet(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function sessionSet(key: string, value: string): void {
  try {
    window.sessionStorage.setItem(key, value);
  } catch {
    /* ignore */
  }
}

function randomId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID().replace(/-/g, "");
  }
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
}

function getAnonymousId(): string {
  const existing = storageGet(ANON_KEY);
  if (existing && existing.length >= 8) return existing;
  const next = randomId().slice(0, 32);
  storageSet(ANON_KEY, next);
  return next;
}

function getSessionId(): string {
  const existing = sessionGet(SESSION_KEY);
  if (existing) return existing;
  const next = randomId().slice(0, 32);
  sessionSet(SESSION_KEY, next);
  return next;
}

function batchUrl(): string {
  return `${API_BASE}/analytics/batch`;
}

function readUtm(): Pick<BatchPayload, "utm_source" | "utm_medium" | "utm_campaign"> {
  const params = new URLSearchParams(window.location.search);
  return {
    utm_source: params.get("utm_source")?.slice(0, 128) || undefined,
    utm_medium: params.get("utm_medium")?.slice(0, 128) || undefined,
    utm_campaign: params.get("utm_campaign")?.slice(0, 128) || undefined,
  };
}

function sanitizeProps(
  props?: Record<string, unknown>
): Record<string, string | number | boolean | null> | undefined {
  if (!props) return undefined;
  const blocked = new Set(["email", "phone", "name", "token", "password"]);
  const out: Record<string, string | number | boolean | null> = {};
  for (const [key, value] of Object.entries(props)) {
    if (blocked.has(key.toLowerCase())) continue;
    if (
      typeof value === "string" ||
      typeof value === "number" ||
      typeof value === "boolean" ||
      value === null
    ) {
      out[key] = value;
    }
  }
  return Object.keys(out).length ? out : undefined;
}

function scheduleFlush(keepalive = false): void {
  if (!hasAnalyticsConsent()) return;
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    debounceTimer = null;
    void flushAnalytics(keepalive);
  }, FLUSH_DEBOUNCE_MS);
}

function enqueue(event: QueuedEvent): void {
  if (!hasAnalyticsConsent()) return;
  queue.push(event);
  if (queue.length >= MAX_BATCH) {
    void flushAnalytics();
    return;
  }
  scheduleFlush();
}

function buildBatch(): BatchPayload {
  const events = queue.splice(0, MAX_BATCH);
  return {
    session_id: getSessionId(),
    anonymous_id: getAnonymousId(),
    platform,
    user_id: userId ?? undefined,
    entry_screen: currentScreen ?? events[0]?.screen,
    referrer: document.referrer?.slice(0, 2048) || undefined,
    ...readUtm(),
    events,
  };
}

async function sendBatch(payload: BatchPayload, keepalive = false): Promise<void> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (INGEST_KEY) {
    headers.Authorization = `Bearer ${INGEST_KEY}`;
  }
  const resp = await fetch(batchUrl(), {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
    keepalive,
    credentials: "omit",
  });
  if (!resp.ok) {
    const detail = await resp.text().catch(() => "");
    throw new Error(`analytics ingest ${resp.status}: ${detail.slice(0, 200)}`);
  }
}

export async function flushAnalytics(keepalive = false): Promise<void> {
  if (!hasAnalyticsConsent() || !queue.length || !API_BASE) return;
  const payload = buildBatch();
  try {
    await sendBatch(payload, keepalive);
  } catch {
    queue.unshift(...payload.events);
  }
}

function leaveCurrentScreen(): void {
  if (!currentScreen || !currentScreenStartedAt) return;
  const duration_ms = Math.max(0, Date.now() - currentScreenStartedAt);
  enqueue({
    name: "screen_leave",
    screen: currentScreen,
    duration_ms,
    ts: new Date().toISOString(),
  });
  currentScreen = null;
  currentScreenStartedAt = 0;
}

export function initProductAnalytics(): void {
  if (typeof window === "undefined") return;
  if (!hasAnalyticsConsent()) return;
  if (initialized) return;
  initialized = true;
  platform = "web";
  getAnonymousId();
  getSessionId();

  if (!sessionStarted) {
    sessionStarted = true;
    enqueue({ name: "session_start", ts: new Date().toISOString() });
  }

  if (!intervalTimer) {
    intervalTimer = setInterval(() => {
      void flushAnalytics();
    }, FLUSH_MS);
  }

  scheduleFlush();

  const onHide = () => {
    leaveCurrentScreen();
    void flushAnalytics(true);
  };
  window.addEventListener("pagehide", onHide);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") onHide();
  });
}

export function setAnalyticsUserId(next: string | null | undefined): void {
  if (!hasAnalyticsConsent()) {
    userId = null;
    return;
  }
  userId = next?.trim() ? next.trim() : null;
}

export function trackScreenView(
  screen: string,
  opts?: { tab?: string; props?: Record<string, unknown> }
): void {
  if (!hasAnalyticsConsent()) return;
  const normalized = screen.slice(0, 64);
  if (currentScreen === normalized) return;
  leaveCurrentScreen();
  currentScreen = normalized;
  currentScreenStartedAt = Date.now();
  enqueue({
    name: "screen_view",
    screen: normalized,
    tab: opts?.tab?.slice(0, 32),
    ts: new Date().toISOString(),
    props: sanitizeProps(opts?.props),
  });
}

/** Map Next.js app pathname to a stable analytics screen slug. */
export function pathnameToScreen(pathname: string): string {
  const parts = pathname.split("/").filter(Boolean);
  if (!parts.length) return "landing";
  if (parts[0] === "auth") return parts.slice(1).join("_").slice(0, 64) || "auth";
  if (parts[0] !== "app") return parts.join("_").slice(0, 64);
  if (parts.length === 1) return "app_home";
  if (parts[1] === "projects" && parts[2]) {
    const section = parts[3] ?? "overview";
    return `project_${section}`.slice(0, 64);
  }
  return parts.slice(1).join("_").slice(0, 64) || "app";
}
