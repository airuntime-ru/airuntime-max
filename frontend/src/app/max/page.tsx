"use client";

/**
 * Mini app entry point.
 *
 * One URL serves both audiences, because MAX attaches exactly one mini app to a bot. The
 * launch parameters decide which one is on screen: a `start_param` means a customer came
 * through `?startapp=<slug>`, anything else means the owner opened it from the bot.
 */

import { useEffect, useState } from "react";

import { OwnerPanel } from "@/components/max/owner-panel";
import { Storefront } from "@/components/max/storefront";
import { getStartParam, isInsideMax, waitForBridge } from "@/lib/max/bridge";

type Route = { kind: "pending" } | { kind: "outside" } | { kind: "owner" } | { kind: "customer"; slug: string };

export default function MaxMiniApp() {
  const [route, setRoute] = useState<Route>({ kind: "pending" });

  useEffect(() => {
    let cancelled = false;
    void waitForBridge().then(() => {
      if (cancelled) return;
      if (!isInsideMax()) {
        setRoute({ kind: "outside" });
        return;
      }
      const slug = getStartParam();
      setRoute(slug ? { kind: "customer", slug } : { kind: "owner" });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (route.kind === "pending") {
    return (
      <div className="max-shell" aria-busy="true" aria-live="polite">
        <div className="max-skeleton" style={{ height: 120 }} />
        <div className="max-skeleton" style={{ height: 18, width: "40%", margin: "24px 0 10px" }} />
        <div className="max-list">
          <div className="max-skeleton" style={{ height: 66 }} />
          <div className="max-skeleton" style={{ height: 66 }} />
        </div>
      </div>
    );
  }

  if (route.kind === "outside") {
    // Reached by pasting the URL into a normal browser. Say so plainly instead of failing
    // on the first API call with a 401 nobody can act on.
    return (
      <div className="max-shell">
        <header className="max-hero">
          <h1>Откройте в MAX</h1>
          <p>Эта страница — мини-приложение мессенджера MAX и работает только внутри него.</p>
        </header>
        <p className="max-note">
          Попросите у владельца ссылку вида max.ru/…?startapp=… или откройте AIRuntime из чата с ботом.
        </p>
      </div>
    );
  }

  return route.kind === "customer" ? <Storefront slug={route.slug} /> : <OwnerPanel />;
}
