/**
 * Consent for non-essential analytics / similar tech.
 * Necessary auth storage (tokens after login) is out of scope here.
 */

export const ANALYTICS_CONSENT_KEY = "airuntime-consent-analytics";

export type AnalyticsConsent = "granted" | "denied";

export function getAnalyticsConsent(): AnalyticsConsent | null {
  if (typeof window === "undefined") return null;
  try {
    const value = window.localStorage.getItem(ANALYTICS_CONSENT_KEY);
    if (value === "granted" || value === "denied") return value;
  } catch {
    /* ignore */
  }
  return null;
}

export function subscribeAnalyticsConsent(onChange: () => void): () => void {
  window.addEventListener("airuntime:consent", onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener("airuntime:consent", onChange);
    window.removeEventListener("storage", onChange);
  };
}

export function setAnalyticsConsent(value: AnalyticsConsent): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(ANALYTICS_CONSENT_KEY, value);
  } catch {
    /* ignore */
  }
  window.dispatchEvent(new CustomEvent("airuntime:consent", { detail: { analytics: value } }));
}

export function hasAnalyticsConsent(): boolean {
  return getAnalyticsConsent() === "granted";
}
