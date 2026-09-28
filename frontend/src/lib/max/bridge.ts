/**
 * MAX Bridge wrapper.
 *
 * The SDK is a CDN script that attaches `window.WebApp`, so everything here has to cope
 * with three realities at once: the script may not have loaded yet, the page may be open
 * in a plain browser (during development, or when someone pastes the URL), and the client
 * may be an older MAX build without a given method. Every accessor therefore degrades to
 * a sane value rather than throwing - a storefront that blanks out because a brightness
 * API is missing would be worse than one that simply renders.
 */

export const MAX_BRIDGE_SRC = "https://st.max.ru/js/max-web-app.js";

export type MaxPlatform = "ios" | "android" | "desktop" | "web" | "unknown";

export type MaxUser = {
  id: number;
  first_name?: string;
  last_name?: string;
  username?: string | null;
  language_code?: string;
  photo_url?: string | null;
};

type MaxInitDataUnsafe = {
  user?: MaxUser;
  chat?: { id: number; type: "DIALOG" | "CHAT" | "CHANNEL" };
  start_param?: string;
  auth_date?: number;
};

type MaxContactResponse = { phone: string; authDate: string; hash: string };

type MaxWebApp = {
  initData?: string;
  initDataUnsafe?: MaxInitDataUnsafe;
  platform?: string;
  version?: string;
  getViewportSize?: () => Promise<{ height: string; width: string }>;
  getLaunchContext?: () => Promise<{ entryPoint: "tabbar" | "default" }>;
  requestContact?: () => Promise<MaxContactResponse>;
  shareMaxContent?: (params: { text?: string; link?: string }) => unknown;
  shareContent?: (params: { text?: string; link?: string }) => unknown;
  openMaxLink?: (url: string) => unknown;
  openLink?: (url: string) => unknown;
  enableClosingConfirmation?: () => void;
  disableClosingConfirmation?: () => void;
  BackButton?: {
    show?: () => void;
    hide?: () => void;
    onClick?: (callback: () => void) => void;
    offClick?: (callback: () => void) => void;
  };
  HapticFeedback?: {
    impactOccurred?: (style: "soft" | "light" | "medium" | "heavy" | "rigid") => void;
    notificationOccurred?: (type: "success" | "warning" | "error") => void;
  };
};

declare global {
  interface Window {
    WebApp?: MaxWebApp;
  }
}

function webApp(): MaxWebApp | null {
  if (typeof window === "undefined") return null;
  return window.WebApp ?? null;
}

/** Resolves once `window.WebApp` exists, or after `timeoutMs` if it never shows up. */
export function waitForBridge(timeoutMs = 4000): Promise<MaxWebApp | null> {
  if (typeof window === "undefined") return Promise.resolve(null);
  if (window.WebApp) return Promise.resolve(window.WebApp);

  return new Promise((resolve) => {
    const started = Date.now();
    const tick = () => {
      if (window.WebApp) {
        resolve(window.WebApp);
        return;
      }
      if (Date.now() - started >= timeoutMs) {
        resolve(null);
        return;
      }
      window.setTimeout(tick, 80);
    };
    tick();
  });
}

/** The signed launch string. Sent to the backend as-is; never parsed for identity here. */
export function getInitData(): string {
  return webApp()?.initData ?? "";
}

/** Display-only launch data. The backend re-derives all of this from the signed string. */
export function getInitDataUnsafe(): MaxInitDataUnsafe {
  return webApp()?.initDataUnsafe ?? {};
}

export function getPlatform(): MaxPlatform {
  const platform = webApp()?.platform;
  if (platform === "ios" || platform === "android" || platform === "desktop" || platform === "web") {
    return platform;
  }
  return "unknown";
}

export function isInsideMax(): boolean {
  return Boolean(webApp()?.initData);
}

/**
 * The slug of the storefront to show.
 *
 * `start_param` is what MAX fills from `?startapp=<slug>`; the query string is the
 * fallback that keeps the page testable in a browser and survives a client that opened
 * the URL directly.
 */
export function getStartParam(): string {
  const fromBridge = (getInitDataUnsafe().start_param ?? "").trim();
  if (fromBridge) return fromBridge;
  if (typeof window === "undefined") return "";
  // MAX also hands the payload to the mini app's own URL as `WebAppStartParam`.
  const query = new URLSearchParams(window.location.search);
  return (query.get("WebAppStartParam") ?? query.get("startapp") ?? "").trim();
}

/** MAX reports its own usable height; on desktop the webview can be shorter than the page. */
export async function getViewportHeight(): Promise<number | null> {
  const app = webApp();
  if (!app?.getViewportSize) return null;
  try {
    const size = await app.getViewportSize();
    const height = Number.parseFloat(String(size.height));
    return Number.isFinite(height) && height > 0 ? height : null;
  } catch {
    return null;
  }
}

/** 'tabbar' when launched from the MAX tab bar rather than a chat. */
export async function getEntryPoint(): Promise<"tabbar" | "default"> {
  const app = webApp();
  if (!app?.getLaunchContext) return "default";
  try {
    const context = await app.getLaunchContext();
    return context.entryPoint === "tabbar" ? "tabbar" : "default";
  } catch {
    return "default";
  }
}

/**
 * Ask MAX for the user's phone number.
 *
 * Returns the platform's signed triple untouched: the backend re-checks the hash against
 * the bot token, so a number that never went through this dialog cannot be passed off as
 * a verified one.
 */
export async function requestContact(): Promise<MaxContactResponse | null> {
  const app = webApp();
  if (!app?.requestContact) return null;
  try {
    const contact = await app.requestContact();
    return contact?.phone ? contact : null;
  } catch {
    // The user declining is the common case, and it is not an error worth surfacing.
    return null;
  }
}

/**
 * Share a storefront link into a MAX chat.
 *
 * `shareMaxContent` opens MAX's own "send to a chat" screen - the natural way to hand a
 * link to clients who are already in MAX. MAX only honours it from a real tap, so call it
 * straight from a click handler. Returns false when there is no way to share, and the
 * caller falls back to copying.
 */
export async function shareInMax(text: string, link: string): Promise<boolean> {
  const app = webApp();
  try {
    if (app?.shareMaxContent) {
      await app.shareMaxContent({ text, link });
      return true;
    }
    if (typeof navigator !== "undefined" && typeof navigator.share === "function") {
      await navigator.share({ text, url: link });
      return true;
    }
  } catch {
    // Dismissing the share sheet rejects; that is the user's choice, not a failure.
    return true;
  }
  return false;
}

/**
 * Open a MAX profile / dialog from a tap.
 *
 * `openMaxLink` keeps https://max.ru/… inside the client. Anything else (including the
 * `max://user/<id>` mention scheme) goes through `openLink`, which the client still
 * handles as a deeplink rather than dumping it in Safari.
 */
export function openMaxChat(url: string): boolean {
  const target = url.trim();
  if (!target) return false;
  const app = webApp();
  try {
    if (target.startsWith("https://max.ru/") && app?.openMaxLink) {
      void app.openMaxLink(target);
      return true;
    }
    if (app?.openLink) {
      void app.openLink(target);
      return true;
    }
    window.location.assign(target);
    return true;
  } catch {
    return false;
  }
}

/**
 * Show MAX's own back button in the header while `onBack` is set; hide it again on
 * cleanup. The returned function undoes both, so it drops straight into an effect.
 */
export function attachHeaderBack(onBack: (() => void) | null): () => void {
  const button = webApp()?.BackButton;
  if (!onBack || !button?.onClick) return () => {};
  button.onClick(onBack);
  button.show?.();
  return () => {
    button.offClick?.(onBack);
    button.hide?.();
  };
}

/** A light tap on phones; desktop and web clients have no motor and ignore it. */
export function haptic(kind: "tap" | "success" | "error"): void {
  const feedback = webApp()?.HapticFeedback;
  try {
    if (kind === "tap") feedback?.impactOccurred?.("light");
    else feedback?.notificationOccurred?.(kind);
  } catch {
    // Older clients throw instead of ignoring; a missing buzz is not worth an error.
  }
}

/** Ask before closing while something is in flight - a storefront half-built by the model
 *  should not vanish because the owner swiped the app away by accident. */
export function guardClosing(on: boolean): void {
  const app = webApp();
  try {
    if (on) app?.enableClosingConfirmation?.();
    else app?.disableClosingConfirmation?.();
  } catch {
    // Not every client has it; the build still finishes server-side either way.
  }
}
