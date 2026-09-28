"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, KeyRound, Loader2, Trash2, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  deleteByokCredential,
  listByokCredentials,
  saveByokCredential,
  testByokCredential,
  type ByokCredentialType,
} from "@/lib/api";
import { cn } from "@/lib/cn";

const PROVIDER_LABELS: Record<string, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  gemini: "Google Gemini",
  openrouter: "OpenRouter",
  routerai: "RouterAI",
};

export function ByokSection() {
  const [items, setItems] = useState<ByokCredentialType[]>([]);
  const [supported, setSupported] = useState<string[]>([]);
  const [provider, setProvider] = useState("openai");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await listByokCredentials();
      setItems(data.items);
      setSupported(data.supported);
    } catch {
      setSupported(["openai"]);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const onSave = async () => {
    if (!apiKey.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await saveByokCredential(provider, apiKey.trim());
      // Never keep the raw key in component state after it has been sent.
      setApiKey("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить ключ");
    } finally {
      setBusy(false);
    }
  };

  const onTest = async (name: string) => {
    setBusy(true);
    try {
      await testByokCredential(name);
      await load();
    } finally {
      setBusy(false);
    }
  };

  const onDelete = async (name: string) => {
    setBusy(true);
    try {
      await deleteByokCredential(name);
      await load();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card hover={false} className="p-5 sm:p-6" id="byok">
      <div className="flex items-start gap-3">
        <span
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[0.7rem] bg-[image:var(--ar-accent-gradient-soft)] text-[var(--ar-sky)]"
          aria-hidden
        >
          <KeyRound size={18} />
        </span>
        <div className="min-w-0">
          <h2 className="font-semibold text-[var(--ar-black)]">Свой API-ключ</h2>
          <p className="mt-1 text-sm leading-relaxed text-[var(--ar-mist)]">
            На своём ключе токены оплачивает провайдер напрямую — кредиты не списываются, и
            доступны любые модели. Ключ хранится зашифрованно и не показывается обратно.
            Ключ OpenAI идёт на api.openai.com; ключ RouterAI — на routerai.ru.
          </p>
        </div>
      </div>

      {items.length > 0 ? (
        <ul className="mt-5 space-y-2">
          {items.map((item) => (
            <li
              key={item.id}
              className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-[0.7rem] border border-black/[0.06] px-3.5 py-2.5"
            >
              <div className="min-w-0">
                <p className="text-sm font-medium text-[var(--ar-black)]">
                  {PROVIDER_LABELS[item.provider] ?? item.provider}
                  <span className="ml-2 font-mono text-xs text-[var(--ar-stone)]">
                    ····{item.last4}
                  </span>
                </p>
                <p
                  className={cn(
                    "mt-0.5 flex items-center gap-1.5 text-xs",
                    item.is_valid ? "text-emerald-600" : "text-amber-700"
                  )}
                >
                  {item.is_valid ? <CheckCircle2 size={13} /> : <TriangleAlert size={13} />}
                  {item.is_valid ? "Ключ работает" : (item.last_error ?? "Ключ не проверен")}
                </p>
              </div>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={() => void onTest(item.provider)}
                >
                  Проверить
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy}
                  onClick={() => void onDelete(item.provider)}
                  aria-label="Удалить ключ"
                >
                  <Trash2 size={15} />
                </Button>
              </div>
            </li>
          ))}
        </ul>
      ) : null}

      <div className="mt-5 flex flex-col gap-2 sm:flex-row">
        <select
          value={provider}
          onChange={(event) => setProvider(event.target.value)}
          aria-label="Провайдер"
          className="h-11 rounded-[0.7rem] border border-black/10 bg-white px-3 text-sm text-[var(--ar-black)] outline-none focus:border-black/30 sm:w-44"
        >
          {supported.map((name) => (
            <option key={name} value={name}>
              {PROVIDER_LABELS[name] ?? name}
            </option>
          ))}
        </select>
        <Input
          type="password"
          autoComplete="off"
          placeholder="sk-…"
          value={apiKey}
          onChange={(event) => setApiKey(event.target.value)}
          className="flex-1"
        />
        <Button variant="accent" disabled={busy || !apiKey.trim()} onClick={() => void onSave()}>
          {busy ? <Loader2 size={15} className="animate-spin" /> : null}
          Сохранить
        </Button>
      </div>
      {error ? <p className="mt-2 text-sm text-rose-600">{error}</p> : null}
    </Card>
  );
}
