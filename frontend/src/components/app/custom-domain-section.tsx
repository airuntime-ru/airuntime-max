"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Clock, Globe, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  getCustomDomain,
  setCustomDomain,
  verifyCustomDomain,
  type CustomDomainType,
} from "@/lib/api";

/** Copy-pasteable DNS row. */
function DnsRow({ type, host, value }: { type: string; host: string; value: string }) {
  return (
    <div className="grid gap-1 rounded-[0.7rem] border border-black/[0.06] bg-[#f7f9fd] px-3.5 py-2.5 sm:grid-cols-[4rem_1fr_1fr] sm:items-center sm:gap-3">
      <span className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--ar-stone)]">
        {type}
      </span>
      <span className="truncate font-mono text-[0.8rem] text-[var(--ar-graphite)]">{host}</span>
      <span className="truncate font-mono text-[0.8rem] text-[var(--ar-black)]">{value}</span>
    </div>
  );
}

export function CustomDomainSection({ projectId }: { projectId: string }) {
  const [state, setState] = useState<CustomDomainType | null>(null);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await getCustomDomain(projectId);
      setState(data);
      setValue(data.custom_domain ?? "");
    } catch {
      setState(null);
    }
  }, [projectId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const run = async (action: () => Promise<CustomDomainType>) => {
    setBusy(true);
    setError(null);
    try {
      setState(await action());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить домен");
    } finally {
      setBusy(false);
    }
  };

  const verified = state?.status === "verified";
  const pending = state?.status === "pending_dns";

  return (
    <Card hover={false} className="p-5 sm:p-6">
      <div className="flex items-start gap-3">
        <span
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[0.7rem] bg-[image:var(--ar-accent-gradient-soft)] text-[var(--ar-sky)]"
          aria-hidden
        >
          <Globe size={18} />
        </span>
        <div className="min-w-0">
          <h3 className="font-semibold text-[var(--ar-black)]">Свой домен</h3>
          <p className="mt-1 text-sm leading-relaxed text-[var(--ar-mist)]">
            Подключите собственный домен — доступно на любом тарифе. Пропишите запись в DNS и
            нажмите «Проверить DNS».
          </p>
        </div>
      </div>

      <div className="mt-5 flex flex-col gap-2 sm:flex-row">
        <Input
          placeholder="shop.example.com"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          className="flex-1"
        />
        <Button
          variant="accent"
          disabled={busy || !value.trim()}
          onClick={() => void run(() => setCustomDomain(projectId, value.trim()))}
        >
          {busy ? <Loader2 size={15} className="animate-spin" /> : null}
          Сохранить
        </Button>
        {state?.custom_domain ? (
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => void run(() => setCustomDomain(projectId, null))}
          >
            Отключить
          </Button>
        ) : null}
      </div>

      {error ? <p className="mt-2 text-sm text-rose-600">{error}</p> : null}

      {state?.custom_domain ? (
        <div className="mt-5 space-y-3">
          <p
            className={`flex items-center gap-2 text-sm ${
              verified ? "text-emerald-700" : "text-amber-800"
            }`}
          >
            {verified ? <CheckCircle2 size={15} /> : <Clock size={15} />}
            {verified
              ? `Домен подтверждён — сайт открывается на ${state.custom_domain}`
              : (state.error ?? "Ждём, пока DNS обновится")}
          </p>

          {!verified ? (
            <>
              <p className="text-[0.68rem] font-semibold uppercase tracking-[0.14em] text-[var(--ar-stone)]">
                Добавьте в DNS одну из записей
              </p>
              <div className="space-y-2">
                {state.target.cname_target ? (
                  <DnsRow
                    type="CNAME"
                    host={state.custom_domain}
                    value={state.target.cname_target}
                  />
                ) : null}
                {state.target.a_record_ip ? (
                  <DnsRow type="A" host={state.custom_domain} value={state.target.a_record_ip} />
                ) : null}
              </div>
            </>
          ) : null}

          {pending || verified ? (
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => void run(() => verifyCustomDomain(projectId))}
            >
              Проверить DNS
            </Button>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}
