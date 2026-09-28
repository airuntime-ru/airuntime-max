"use client";

import { useCallback, useEffect, useId, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import {
  ArrowLeft,
  BookOpen,
  Bot,
  FolderOpen,
  GitBranch,
  Globe,
  KeyRound,
  Layers,
  LayoutDashboard,
  Loader2,
  Menu,
  MessageSquare,
  Rocket,
  ScrollText,
  Settings,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { ProjectStatusChip } from "@/components/app/project-status-chip";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { PageLoader } from "@/components/ui/loader";
import { getProject, type ProjectType } from "@/lib/api";
import {
  getActiveProjectChatStream,
  subscribeProjectStreams,
  type ChatStreamSnapshot,
} from "@/lib/chat-stream-runtime";
import { cn } from "@/lib/cn";
import { projectTypeLabel } from "@/lib/project-status";
import { usePageVisible } from "@/lib/use-page-visible";

const TYPE_ICONS: Record<string, LucideIcon> = {
  website: Globe,
  telegram_bot: Bot,
  mixed: Layers,
};

const STATUS_GUIDANCE: Record<
  string,
  (project: ProjectType) => {
    title: string;
    body: string;
    showSecrets?: boolean;
    showChat?: boolean;
    showHelp?: boolean;
  }
> = {
  needs_configuration: (project) => ({
    title: "Нужна настройка",
    body:
      project.type === "telegram_bot" || project.type === "mixed"
        ? "Код бота уже есть в проекте, но запустить его пока нельзя: не хватает токена. Откройте секреты проекта, вставьте TELEGRAM_BOT_TOKEN (его выдаёт @BotFather в Telegram после команды /newbot) и запуск продолжится автоматически."
        : "Проекту не хватает данных для запуска — откройте настройки проекта и заполните то, что запрашивается в разделе «Ключи и токены».",
    showSecrets: true,
    showChat: true,
    showHelp: project.type === "telegram_bot" || project.type === "mixed",
  }),
  blocked: (project) => ({
    title: "Заблокирован модерацией",
    body:
      "Проект остановлен автоматической проверкой безопасности" +
      (project.blocked_reason ? `: ${project.blocked_reason}.` : ".") +
      " Если считаете это ошибкой, напишите в поддержку — решение может принять только администратор.",
  }),
};

type NavItem = { href: string; label: string; icon: LucideIcon; exact?: boolean };

function navActive(pathname: string | null, item: NavItem) {
  if (!pathname) return false;
  if (item.exact) return pathname === item.href;
  return pathname === item.href || pathname.startsWith(`${item.href}/`);
}

function streamBannerKey(snap: ChatStreamSnapshot | null) {
  if (!snap) return "";
  return `${snap.loading}:${snap.agentStatus?.phase}:${snap.agentStatus?.label}:${snap.agentStatus?.state}`;
}

const bannerCache = new Map<string, { key: string; snap: ChatStreamSnapshot | null }>();

function getBannerSnapshot(projectId: string): ChatStreamSnapshot | null {
  const snap = getActiveProjectChatStream(projectId);
  const key = streamBannerKey(snap);
  const prev = bannerCache.get(projectId);
  if (prev && prev.key === key) return prev.snap;
  bannerCache.set(projectId, { key, snap });
  return snap;
}

function ProjectNavLinks({
  items,
  pathname,
  onNavigate,
}: {
  items: NavItem[];
  pathname: string | null;
  onNavigate?: () => void;
}) {
  return (
    <nav aria-label="Разделы проекта" className="flex flex-col gap-0.5">
      {items.map((item) => {
        const active = navActive(pathname, item);
        return (
          <Link
            key={item.href}
            href={item.href}
            onClick={onNavigate}
            className={cn(
              "flex min-h-10 items-center gap-2.5 rounded-[0.7rem] px-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ar-sky)]/35",
              active
                ? "bg-[var(--ar-black)] text-white"
                : "text-[var(--ar-mist)] hover:bg-black/[0.04] hover:text-[var(--ar-black)]"
            )}
          >
            <item.icon size={16} aria-hidden />
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}

export default function ProjectLayout({ children }: { children: React.ReactNode }) {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const pathname = usePathname();
  const projectId = params.id;
  const pageVisible = usePageVisible();
  const menuId = useId();
  const [project, setProject] = useState<ProjectType | null>(null);
  const [statusModalOpen, setStatusModalOpen] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [navPath, setNavPath] = useState(pathname);
  const onChatTab = pathname?.includes("/chat") ?? false;

  if (navPath !== pathname) {
    setNavPath(pathname);
    setMobileNavOpen(false);
  }

  const subscribeChatBanner = useCallback(
    (onStoreChange: () => void) => {
      if (!projectId || onChatTab) return () => {};
      return subscribeProjectStreams(projectId, onStoreChange);
    },
    [projectId, onChatTab]
  );
  const getChatBannerSnapshot = useCallback(() => {
    if (!projectId || onChatTab) return null;
    return getBannerSnapshot(projectId);
  }, [projectId, onChatTab]);
  const chatStream = useSyncExternalStore(
    subscribeChatBanner,
    getChatBannerSnapshot,
    () => null
  );

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    void (async () => {
      if (!projectId) return;
      try {
        const row = await getProject(projectId);
        if (active) setProject(row);
      } catch {
        if (active) setProject(null);
      }
    })();
    return () => {
      active = false;
      controller.abort();
    };
  }, [projectId]);

  useEffect(() => {
    if (!projectId || !pageVisible) return undefined;
    if (project?.status !== "needs_configuration" && project?.status !== "deploying") {
      return undefined;
    }
    const timer = window.setInterval(() => {
      void getProject(projectId).then(setProject).catch(() => {});
    }, 4000);
    return () => window.clearInterval(timer);
  }, [projectId, project?.status, pageVisible]);

  useEffect(() => {
    if (!mobileNavOpen) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileNavOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [mobileNavOpen]);

  if (!project) {
    return <PageLoader />;
  }

  const base = `/app/projects/${projectId}`;
  const tabs: NavItem[] = [
    { href: base, label: "Обзор", icon: LayoutDashboard, exact: true },
    { href: `${base}/chat`, label: "Чат", icon: MessageSquare },
    { href: `${base}/orchestration`, label: "Оркестрация", icon: GitBranch },
    { href: `${base}/deployments`, label: "Деплои", icon: Rocket },
    { href: `${base}/versions`, label: "Файлы", icon: FolderOpen },
    { href: `${base}/logs`, label: "Логи", icon: ScrollText },
    { href: `${base}/settings`, label: "Настройки", icon: Settings },
  ];
  const currentTab = tabs.find((item) => navActive(pathname, item)) ?? tabs[0];

  const guidance = STATUS_GUIDANCE[project.status]?.(project);
  const showStreamBanner = Boolean(chatStream?.loading && chatStream.agentStatus && !onChatTab);
  const TypeIcon = TYPE_ICONS[project.type] ?? Globe;

  const sidebarBody = (
    <>
      <Link
        href="/app"
        className="inline-flex min-h-9 items-center gap-1.5 rounded-[0.7rem] px-2 text-sm text-[var(--ar-stone)] transition-colors hover:bg-black/[0.04] hover:text-[var(--ar-black)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ar-sky)]/35"
      >
        <ArrowLeft size={15} aria-hidden />
        Все проекты
      </Link>

      <div className="mt-4 flex items-start gap-3 px-1">
        <span
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[0.7rem] bg-[image:var(--ar-accent-gradient-soft)] text-[var(--ar-sky)]"
          aria-hidden
        >
          <TypeIcon size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <h1 className="text-base font-semibold leading-snug tracking-[-0.02em] text-[var(--ar-black)]">
            {project.name}
          </h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {guidance ? (
              <button type="button" onClick={() => setStatusModalOpen(true)}>
                <ProjectStatusChip status={project.status} className="cursor-pointer" />
              </button>
            ) : (
              <ProjectStatusChip status={project.status} />
            )}
            <span className="rounded-full border border-black/[0.07] bg-black/[0.02] px-2 py-0.5 text-[11px] text-[var(--ar-stone)]">
              {projectTypeLabel(project.type)}
            </span>
          </div>
        </div>
      </div>

      <div className="mt-5 flex-1">
        <ProjectNavLinks
          items={tabs}
          pathname={pathname}
          onNavigate={() => setMobileNavOpen(false)}
        />
      </div>
    </>
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col lg:flex-row lg:gap-4">
      <aside className="hidden w-[15.5rem] shrink-0 flex-col lg:flex">
        <div className="flex min-h-0 flex-1 flex-col rounded-[0.95rem] border border-black/[0.07] bg-white/80 p-3 shadow-[0_1px_2px_rgba(15,23,42,0.04)] backdrop-blur-sm">
          {sidebarBody}
        </div>
      </aside>

      {/* Mobile: one slim bar instead of the old header+tabs stack. */}
      <div className="mb-3 flex items-center gap-2 lg:hidden">
        <button
          type="button"
          onClick={() => setMobileNavOpen(true)}
          className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-[0.7rem] border border-black/[0.08] bg-white text-[var(--ar-graphite)]"
          aria-expanded={mobileNavOpen}
          aria-controls={menuId}
          aria-label="Меню проекта"
        >
          <Menu size={18} aria-hidden />
        </button>
        <div className="min-w-0 flex-1 rounded-[0.7rem] border border-black/[0.08] bg-white px-3 py-2">
          <p className="truncate text-sm font-semibold text-[var(--ar-black)]">{project.name}</p>
          <p className="truncate text-xs text-[var(--ar-stone)]">{currentTab.label}</p>
        </div>
        {!onChatTab ? (
          <Link
            href={`${base}/chat`}
            className="btn-glow inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-[0.7rem]"
            aria-label="Чат"
          >
            <MessageSquare size={16} aria-hidden />
          </Link>
        ) : null}
      </div>

      {mobileNavOpen ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            type="button"
            className="absolute inset-0 bg-[rgba(4,8,20,0.45)] backdrop-blur-[3px]"
            aria-label="Закрыть меню"
            onClick={() => setMobileNavOpen(false)}
          />
          <div
            id={menuId}
            role="dialog"
            aria-modal="true"
            aria-label="Меню проекта"
            className="absolute inset-y-0 left-0 flex w-[min(20rem,88vw)] flex-col bg-white p-4 shadow-xl"
          >
            <div className="mb-2 flex justify-end">
              <button
                type="button"
                onClick={() => setMobileNavOpen(false)}
                className="inline-flex h-10 w-10 items-center justify-center rounded-full text-[var(--ar-stone)] hover:bg-black/[0.05]"
                aria-label="Закрыть"
              >
                <X size={18} aria-hidden />
              </button>
            </div>
            {sidebarBody}
          </div>
        </div>
      ) : null}

      <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3">
        {showStreamBanner && chatStream?.agentStatus ? (
          <button
            type="button"
            onClick={() => router.push(`${base}/chat`)}
            className="flex w-full min-h-11 items-center gap-3 rounded-[var(--ar-radius-md)] border border-sky-200 bg-sky-50 px-4 py-2.5 text-left transition hover:bg-sky-100/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ar-sky)]/35"
          >
            <Loader2 size={16} className="shrink-0 animate-spin text-[var(--ar-sky)]" aria-hidden />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium text-[var(--ar-black)]">
                {chatStream.agentStatus.label}
              </span>
              <span className="block text-xs text-[var(--ar-stone)]">Агент работает — открыть чат</span>
            </span>
            <MessageSquare size={16} className="shrink-0 text-[var(--ar-sky)]" aria-hidden />
          </button>
        ) : null}

        <div className="flex min-h-0 flex-1 flex-col">{children}</div>
      </div>

      {guidance ? (
        <Modal open={statusModalOpen} onClose={() => setStatusModalOpen(false)} title={guidance.title}>
          <div className="space-y-5">
            <div className="flex items-start gap-3">
              <span
                className={cn(
                  "flex h-10 w-10 shrink-0 items-center justify-center rounded-[var(--ar-radius-sm)]",
                  project.status === "blocked" ? "bg-rose-50 text-rose-600" : "bg-amber-50 text-amber-600"
                )}
              >
                <KeyRound size={18} aria-hidden />
              </span>
              <p className="text-sm leading-7 text-[var(--ar-mist)]">{guidance.body}</p>
            </div>
            {guidance.showSecrets || guidance.showChat || guidance.showHelp ? (
              <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-end">
                {guidance.showChat ? (
                  <Button
                    variant="outline"
                    onClick={() => {
                      setStatusModalOpen(false);
                      router.push(`${base}/chat`);
                    }}
                  >
                    <MessageSquare size={16} />
                    В чат
                  </Button>
                ) : null}
                {guidance.showHelp ? (
                  <Button
                    variant="outline"
                    onClick={() => {
                      setStatusModalOpen(false);
                      router.push(`/help/telegram-token?projectId=${encodeURIComponent(projectId)}`);
                    }}
                  >
                    <BookOpen size={16} />
                    Как получить токен
                  </Button>
                ) : null}
                {guidance.showSecrets ? (
                  <Button
                    variant="accent"
                    onClick={() => {
                      setStatusModalOpen(false);
                      router.push(`${base}/settings#secrets`);
                    }}
                  >
                    <Settings size={16} />
                    В секреты
                  </Button>
                ) : null}
              </div>
            ) : null}
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
