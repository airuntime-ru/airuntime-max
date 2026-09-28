"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Bot, Database, ExternalLink, Globe2, ImageUp, Trash2 } from "lucide-react";

import { ProjectSecretsSection } from "@/components/app/project-secrets-section";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { PageLoader } from "@/components/ui/loader";
import { Textarea } from "@/components/ui/textarea";
import { CustomDomainSection } from "@/components/app/custom-domain-section";
import { projectTypeLabel } from "@/lib/project-status";
import {
  deleteProject,
  getProject,
  getTelegramBotProfile,
  updateProject,
  updateTelegramBotAvatar,
  updateTelegramBotProfile,
  type ProjectType,
  type SecretType,
  type TelegramBotProfileType,
} from "@/lib/api";

const baseDomain = process.env.NEXT_PUBLIC_BASE_DOMAIN ?? "airuntime.ru";

/** Display labels for backend `project_services._PRESETS` (postgres/redis/mysql/mongo/rabbitmq). */
const SUPPORTED_SERVICE_PRESETS = ["Postgres", "Redis", "MySQL", "MongoDB", "RabbitMQ"] as const;

function SettingsSection({
  id,
  title,
  description,
  children,
}: {
  id: string;
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-24 space-y-3">
      <div>
        <h2 className="text-base font-semibold tracking-[-0.02em] text-[var(--ar-black)]">{title}</h2>
        {description ? <p className="mt-1 text-sm text-[var(--ar-mist)]">{description}</p> : null}
      </div>
      {children}
    </section>
  );
}

function TelegramBotAppearanceCard({ projectId }: { projectId: string }) {
  const [profile, setProfile] = useState<TelegramBotProfileType | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [shortDescription, setShortDescription] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");

  useEffect(() => {
    let active = true;
    void (async () => {
      setLoading(true);
      setError("");
      try {
        const row = await getTelegramBotProfile(projectId);
        if (!active) return;
        setProfile(row);
        setName(row.name ?? "");
        setDescription(row.description ?? "");
        setShortDescription(row.short_description ?? "");
      } catch (err) {
        if (active) {
          setError(err instanceof Error ? err.message : "Не удалось загрузить настройки бота");
        }
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [projectId]);

  const onSave = async () => {
    setSaving(true);
    setError("");
    setSaved("");
    try {
      const row = await updateTelegramBotProfile(projectId, {
        name: name.trim(),
        description,
        short_description: shortDescription,
      });
      setProfile(row);
      setName(row.name ?? "");
      setDescription(row.description ?? "");
      setShortDescription(row.short_description ?? "");
      setSaved("Настройки бота сохранены в Telegram");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить настройки бота");
    } finally {
      setSaving(false);
    }
  };

  const onAvatarSelected = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setUploading(true);
    setError("");
    setSaved("");
    try {
      const row = await updateTelegramBotAvatar(projectId, file);
      setProfile(row);
      setSaved("Аватар бота обновлен в Telegram");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось обновить аватар бота");
    } finally {
      setUploading(false);
    }
  };

  return (
    <Card hover={false}>
      <div className="flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[var(--ar-radius-sm)] bg-sky-50 text-[var(--ar-sky)]">
          <Bot size={18} aria-hidden />
        </span>
        <div className="min-w-0 flex-1 space-y-4">
          <div>
            <p className="text-sm font-semibold text-[var(--ar-black)]">Оформление Telegram-бота</p>
            <p className="mt-1 text-sm text-[var(--ar-mist)]">
              Имя, описание и аватар в Telegram. Токен хранится отдельно в секретах.
            </p>
            {profile?.username ? (
              <a
                href={`https://t.me/${profile.username.replace(/^@/, "")}`}
                target="_blank"
                rel="noreferrer"
                className="mt-2 inline-flex items-center gap-1 text-sm text-[var(--ar-sky)] hover:underline"
              >
                @{profile.username.replace(/^@/, "")}
                <ExternalLink size={13} aria-hidden />
              </a>
            ) : null}
          </div>

          <div className="grid gap-3">
            <div className="space-y-1.5">
              <label htmlFor="bot-name" className="text-xs font-medium text-[var(--ar-stone)]">
                Имя
              </label>
              <Input
                id="bot-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={64}
                disabled={loading}
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="bot-short" className="text-xs font-medium text-[var(--ar-stone)]">
                Короткое описание
              </label>
              <Input
                id="bot-short"
                value={shortDescription}
                onChange={(event) => setShortDescription(event.target.value)}
                maxLength={120}
                disabled={loading}
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="bot-desc" className="text-xs font-medium text-[var(--ar-stone)]">
                Описание
              </label>
              <Textarea
                id="bot-desc"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                maxLength={512}
                placeholder="Расскажите, что умеет бот и когда им пользоваться."
                disabled={loading}
              />
            </div>
          </div>

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <Button variant="accent" onClick={() => void onSave()} disabled={loading || saving || !name.trim()}>
              {saving ? "Сохраняем..." : "Сохранить оформление"}
            </Button>
            <label className="inline-flex min-h-10 cursor-pointer items-center justify-center gap-2 rounded-[var(--ar-radius-sm)] border border-black/10 bg-white px-4 text-sm font-semibold text-[var(--ar-black)] transition hover:bg-black/5">
              <ImageUp size={16} aria-hidden />
              {uploading ? "Загружаем..." : "Загрузить аватар"}
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                className="hidden"
                disabled={uploading}
                onChange={(event) => void onAvatarSelected(event)}
              />
            </label>
          </div>

          {error ? <p className="text-sm text-rose-600">{error}</p> : null}
          {saved ? <p className="text-sm text-emerald-600">{saved}</p> : null}
        </div>
      </div>
    </Card>
  );
}

export default function ProjectSettingsPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const [project, setProject] = useState<ProjectType | null>(null);
  const [subdomain, setSubdomain] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [secrets, setSecrets] = useState<SecretType[] | null>(null);
  const botTokenConfigured = secrets?.some(
    (secret) => secret.key === "TELEGRAM_BOT_TOKEN" && secret.has_value
  );

  const [deleteStep, setDeleteStep] = useState<0 | 1 | 2>(0);
  const [deleteConfirmName, setDeleteConfirmName] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");

  const onDeleteProject = async () => {
    if (!params.id) return;
    setDeleting(true);
    setDeleteError("");
    try {
      await deleteProject(params.id);
      router.push("/app");
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "Не удалось удалить проект");
      setDeleting(false);
    }
  };

  useEffect(() => {
    let active = true;
    void (async () => {
      if (!params.id) return;
      const row = await getProject(params.id);
      if (!active) return;
      setProject(row);
      setSubdomain(row.deploy_subdomain ?? "");
    })();
    return () => {
      active = false;
    };
  }, [params.id]);

  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const hash = window.location.hash.replace("#", "");
    if (!hash) return undefined;
    const timer = window.setTimeout(() => {
      document.getElementById(hash)?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 80);
    return () => window.clearTimeout(timer);
  }, [project?.id]);

  const previewUrl = useMemo(() => {
    if (subdomain.trim()) {
      return `https://${subdomain.trim()}.${baseDomain}`;
    }
    if (project?.planned_site_url) {
      return project.planned_site_url;
    }
    return `https://ваш-поддомен.${baseDomain}`;
  }, [subdomain, project?.planned_site_url]);

  const onSaveSubdomain = async () => {
    if (!project || !params.id) return;
    setSaving(true);
    setError("");
    setSaved(false);
    try {
      const updated = await updateProject(params.id, {
        deploy_subdomain: subdomain.trim() || null,
      });
      setProject(updated);
      setSubdomain(updated.deploy_subdomain ?? "");
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить поддомен");
    } finally {
      setSaving(false);
    }
  };

  if (!project || !params.id) return <PageLoader />;

  const hasTelegramTokenSlot =
    secrets?.some((secret) => secret.key === "TELEGRAM_BOT_TOKEN") ?? false;
  const showSubdomainCard =
    Boolean(project.planned_site_url) &&
    !(project.type === "website" && hasTelegramTokenSlot);
  const showBotCard =
    (project.type === "telegram_bot" || project.type === "mixed") && botTokenConfigured;

  const nav = [
    { href: "#general", label: "Основное" },
    { href: "#secrets", label: "Секреты" },
    { href: "#services", label: "Сервисы" },
    { href: "#publish", label: "Публикация" },
    { href: "#danger", label: "Опасная зона" },
  ];

  return (
    <div className="space-y-8">
      <nav
        aria-label="Разделы настроек"
        className="flex gap-2 overflow-x-auto pb-1 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {nav.map((item) => (
          <a
            key={item.href}
            href={item.href}
            className="shrink-0 rounded-full border border-black/[0.07] bg-white px-3.5 py-1.5 text-sm font-medium text-[var(--ar-mist)] transition-colors hover:border-[var(--ar-sky)]/35 hover:text-[var(--ar-black)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ar-sky)]/35"
          >
            {item.label}
          </a>
        ))}
      </nav>

      <SettingsSection
        id="general"
        title="Основное"
        description="Тип проекта и связанные точки входа."
      >
        <Card hover={false} className="grid gap-3 sm:grid-cols-2">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.12em] text-[var(--ar-stone)]">Название</p>
            <p className="mt-1 text-sm font-medium text-[var(--ar-black)]">{project.name}</p>
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.12em] text-[var(--ar-stone)]">Тип</p>
            <p className="mt-1 text-sm font-medium text-[var(--ar-black)]">{projectTypeLabel(project.type)}</p>
          </div>
          {project.description ? (
            <div className="sm:col-span-2">
              <p className="text-xs font-medium uppercase tracking-[0.12em] text-[var(--ar-stone)]">Описание</p>
              <p className="mt-1 text-sm text-[var(--ar-mist)]">{project.description}</p>
            </div>
          ) : null}
        </Card>
        {showBotCard ? <TelegramBotAppearanceCard projectId={params.id} /> : null}
      </SettingsSection>

      <SettingsSection
        id="secrets"
        title="Переменные и секреты"
        description="Токены и ключи вводятся здесь. Значения не показываются открытым текстом и не уходят в LLM."
      >
        <ProjectSecretsSection projectId={params.id} onChange={setSecrets} />
      </SettingsSection>

      <SettingsSection
        id="services"
        title="Сервисы"
        description="Базы и очереди агент запрашивает через платформу во время сборки — здесь ничего подключать вручную не нужно."
      >
        <Card hover={false}>
          <div className="flex items-start gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[var(--ar-radius-sm)] bg-black/[0.03] text-[var(--ar-graphite)]">
              <Database size={18} aria-hidden />
            </span>
            <div className="min-w-0 space-y-2">
              <p className="text-sm font-semibold text-[var(--ar-black)]">Что умеет платформа</p>
              <p className="text-sm leading-relaxed text-[var(--ar-mist)]">
                Если проекту нужна база или очередь, агент запросит её в чате. Креды создаются
                автоматически и подставляются в окружение контейнера. Отдельная ручная настройка
                здесь не нужна. Список ниже — какие типы платформа умеет поднимать сама; это не
                статус подключений этого проекта.
              </p>
              <p className="pt-1 text-xs font-medium uppercase tracking-[0.12em] text-[var(--ar-stone)]">
                Поддерживаемые типы
              </p>
              <ul className="m-0 flex list-none flex-wrap gap-x-3 gap-y-1 p-0">
                {SUPPORTED_SERVICE_PRESETS.map((name) => (
                  <li key={name} className="font-mono text-xs text-[var(--ar-mist)]">
                    {name}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </Card>
      </SettingsSection>

      <SettingsSection
        id="publish"
        title="Домен и публикация"
        description="Адрес на поддомене AIRuntime или ваш собственный домен."
      >
        {showSubdomainCard ? (
          <Card hover={false}>
            <div className="flex items-start gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[var(--ar-radius-sm)] bg-emerald-50 text-emerald-700">
                <Globe2 size={18} aria-hidden />
              </span>
              <div className="min-w-0 flex-1 space-y-4">
                <div>
                  <p className="text-sm font-semibold text-[var(--ar-black)]">Поддомен сайта</p>
                  <p className="mt-1 text-sm leading-7 text-[var(--ar-mist)]">
                    Адрес, на котором откроется сайт после следующего деплоя.
                  </p>
                </div>

                <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
                  <div className="flex-1 space-y-2">
                    <label htmlFor="deploy-subdomain" className="text-xs font-medium text-[var(--ar-stone)]">
                      Поддомен
                    </label>
                    <div className="flex overflow-hidden rounded-[var(--ar-radius-sm)] border border-black/10 bg-white">
                      <Input
                        id="deploy-subdomain"
                        value={subdomain}
                        onChange={(event) => {
                          setSubdomain(event.target.value.toLowerCase());
                          setSaved(false);
                        }}
                        placeholder="my-landing"
                        className="border-0 bg-transparent shadow-none focus:ring-0"
                        autoComplete="off"
                        spellCheck={false}
                      />
                      <span className="flex items-center border-l border-black/10 bg-[#f7f8fa] px-3 text-sm text-[var(--ar-mist)]">
                        .{baseDomain}
                      </span>
                    </div>
                  </div>
                  <Button variant="accent" onClick={() => void onSaveSubdomain()} disabled={saving}>
                    {saving ? "Сохраняем..." : "Сохранить"}
                  </Button>
                </div>

                <p className="text-sm text-[var(--ar-mist)]">
                  Будет доступен по адресу:{" "}
                  <span className="font-medium text-[var(--ar-black)]">{previewUrl}</span>
                </p>
                <p className="text-xs text-[var(--ar-stone)]">
                  Поддомен проверяется на уникальность. Собственный домен подключается ниже.
                </p>
                {project.deployment_url ? (
                  <p className="text-xs leading-6 text-[var(--ar-stone)]">
                    Текущий деплой: {project.deployment_url}. Новый поддомен применится при следующем
                    запуске.
                  </p>
                ) : null}
                {error ? <p className="text-sm text-rose-600">{error}</p> : null}
                {saved ? <p className="text-sm text-emerald-600">Поддомен сохранен</p> : null}
              </div>
            </div>
          </Card>
        ) : (
          <Card hover={false}>
            <p className="text-sm text-[var(--ar-mist)]">
              Для этого проекта публикация идёт через Telegram-бота или без отдельного поддомена.
              {project.deployment_url ? (
                <>
                  {" "}
                  Текущий URL:{" "}
                  <a
                    href={project.deployment_url}
                    target="_blank"
                    rel="noreferrer"
                    className="font-medium text-[var(--ar-sky)] hover:underline"
                  >
                    {project.deployment_url}
                  </a>
                </>
              ) : null}
            </p>
          </Card>
        )}

        <CustomDomainSection projectId={params.id} />
      </SettingsSection>

      <SettingsSection id="danger" title="Опасная зона" description="Необратимые действия с проектом.">
        <Card hover={false} className="border-rose-200 bg-rose-50/40">
          <div className="flex items-start gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[var(--ar-radius-sm)] bg-rose-100 text-rose-600">
              <AlertTriangle size={18} aria-hidden />
            </span>
            <div className="min-w-0 flex-1 space-y-3">
              <div>
                <p className="text-sm font-semibold text-[var(--ar-black)]">Удалить проект</p>
                <p className="mt-1 text-sm leading-7 text-[var(--ar-mist)]">
                  Удаление необратимо: остановится контейнер, пропадут чаты, файлы, деплои и секреты.
                </p>
              </div>
              <Button
                variant="outline"
                className="border-rose-300 text-rose-700 hover:bg-rose-100"
                onClick={() => setDeleteStep(1)}
              >
                <Trash2 size={16} />
                Удалить проект
              </Button>
            </div>
          </div>
        </Card>
      </SettingsSection>

      <Modal
        open={deleteStep === 1}
        onClose={() => setDeleteStep(0)}
        title="Удалить проект?"
        description={`Проект «${project.name}» и всё его содержимое будут удалены безвозвратно.`}
      >
        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button variant="ghost" onClick={() => setDeleteStep(0)}>
            Отмена
          </Button>
          <Button
            variant="outline"
            className="border-rose-300 text-rose-700 hover:bg-rose-100"
            onClick={() => {
              setDeleteConfirmName("");
              setDeleteStep(2);
            }}
          >
            Продолжить удаление
          </Button>
        </div>
      </Modal>

      <Modal
        open={deleteStep === 2}
        onClose={() => setDeleteStep(0)}
        title="Подтвердите удаление"
        description={`Чтобы окончательно удалить проект, введите его название: ${project.name}`}
      >
        <div className="space-y-4">
          <Input
            value={deleteConfirmName}
            onChange={(event) => setDeleteConfirmName(event.target.value)}
            placeholder={project.name}
            autoComplete="off"
          />
          {deleteError ? <p className="text-sm text-rose-600">{deleteError}</p> : null}
          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <Button variant="ghost" onClick={() => setDeleteStep(0)} disabled={deleting}>
              Отмена
            </Button>
            <Button
              variant="outline"
              className="border-rose-300 text-rose-700 hover:bg-rose-100"
              disabled={deleteConfirmName !== project.name || deleting}
              onClick={() => void onDeleteProject()}
            >
              <Trash2 size={16} />
              {deleting ? "Удаляем..." : "Удалить навсегда"}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
