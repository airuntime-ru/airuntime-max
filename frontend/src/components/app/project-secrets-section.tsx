"use client";

import { useCallback, useEffect, useState } from "react";
import { BookOpen, CheckCircle2, KeyRound, Pencil, ShieldCheck, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/loader";
import { Input } from "@/components/ui/input";
import { setSecretValue, listSecrets, type SecretType } from "@/lib/api";

// Known secret keys that have a dedicated setup walkthrough - shown as an "Инструкция" link
// next to the row. Add an entry here whenever a new /help/* page is created for a secret type.
const SECRET_HELP_LINKS: Record<string, string> = {
  TELEGRAM_BOT_TOKEN: "/help/telegram-token",
};

function SecretRow({
  secret,
  projectId,
  onSaved,
}: {
  secret: SecretType;
  projectId: string;
  onSaved: (updated: SecretType) => void;
}) {
  const [value, setValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const helpUrl = SECRET_HELP_LINKS[secret.key];
  const showEditor = !secret.has_value || editing;

  const onSave = async () => {
    if (!value.trim()) return;
    setSaving(true);
    setError("");
    try {
      const updated = await setSecretValue(projectId, secret.id, value);
      setValue("");
      setEditing(false);
      onSaved(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить значение");
    } finally {
      setSaving(false);
    }
  };

  const onCancelEdit = () => {
    setEditing(false);
    setValue("");
    setError("");
  };

  return (
    <div className="flex flex-col gap-3 rounded-[0.7rem] border border-black/[0.07] bg-white px-4 py-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <p className="font-medium text-[var(--ar-black)]">{secret.key}</p>
          {secret.reason ? (
            <p className="mt-0.5 text-xs text-[var(--ar-stone)]">{secret.reason}</p>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {helpUrl ? (
            <a href={helpUrl} target="_blank" rel="noreferrer">
              <Button variant="outline" size="sm">
                <BookOpen size={14} />
                Инструкция
              </Button>
            </a>
          ) : null}
          {secret.has_value && !editing ? (
            <>
              <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-700">
                <CheckCircle2 size={14} />
                Настроено
              </span>
              <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
                <Pencil size={14} />
                Изменить
              </Button>
            </>
          ) : null}
        </div>
      </div>
      {showEditor ? (
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            placeholder={secret.has_value ? "Вставьте новое значение" : "Вставьте значение"}
            type="password"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            autoComplete="off"
          />
          <Button
            variant="accent"
            size="sm"
            className="sm:w-auto"
            onClick={() => void onSave()}
            disabled={saving || !value.trim()}
          >
            <KeyRound size={14} />
            {saving ? "Проверяем..." : secret.has_value ? "Обновить" : "Сохранить"}
          </Button>
          {editing ? (
            <Button variant="outline" size="sm" className="sm:w-auto" onClick={onCancelEdit} disabled={saving}>
              <X size={14} />
              Отмена
            </Button>
          ) : null}
        </div>
      ) : null}
      {error ? <p className="text-sm text-rose-600">{error}</p> : null}
    </div>
  );
}

export function ProjectSecretsSection({
  projectId,
  onChange,
}: {
  projectId: string;
  onChange?: (secrets: SecretType[]) => void;
}) {
  const [secrets, setSecrets] = useState<SecretType[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await listSecrets(projectId);
      setSecrets(rows);
      onChange?.(rows);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить секреты");
    } finally {
      setLoading(false);
    }
  }, [projectId, onChange]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refresh();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  return (
    <Card hover={false} className="md:col-span-2" id="secrets">
      <div className="space-y-4">
        <div className="flex items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[0.7rem] bg-[image:var(--ar-accent-gradient-soft)] text-[var(--ar-sky)]">
            <ShieldCheck size={18} />
          </span>
          <div>
            <p className="text-sm font-semibold text-[var(--ar-black)]">Ключи и токены</p>
            <p className="mt-1 text-sm leading-7 text-[var(--ar-mist)]">
              Когда проекту нужен токен или API-ключ, AIRuntime сам заводит для него слот здесь —
              просто впишите значение. Хранится зашифрованно и доступно только при сборке и
              запуске проекта.
            </p>
          </div>
        </div>

        {error ? <p className="text-sm text-rose-600">{error}</p> : null}

        {loading ? (
          <p className="text-sm text-[var(--ar-stone)]">Загрузка...</p>
        ) : secrets.length === 0 ? (
          <EmptyState
            title="Секретов пока нет"
            description="Как только проекту понадобится токен или ключ API, он появится здесь - опишите задачу в чате."
          />
        ) : (
          <div className="grid gap-2">
            {secrets.map((secret) => (
              <SecretRow
                key={secret.id}
                secret={secret}
                projectId={projectId}
                onSaved={(updated) => {
                  setSecrets((prev) => {
                    const next = prev.map((row) => (row.id === updated.id ? updated : row));
                    onChange?.(next);
                    return next;
                  });
                }}
              />
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}
