"use client";

import { useCallback, useEffect, useMemo, useRef, useState, startTransition } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import {
  ArrowLeft,
  ArrowUp,
  ChevronDown,
  Paperclip,
  Pin,
  PinOff,
  Plus,
  Search,
  Square,
  X,
} from "lucide-react";

import { AgentStatusPanel } from "@/components/chat/message-item";
import { MessageList } from "@/components/chat/message-list";
import { ToolActivityFeed } from "@/components/chat/tool-activity-feed";
import { AutoTextarea } from "@/components/ui/auto-textarea";
import { Button } from "@/components/ui/button";
import {
  createChat,
  createMessage,
  deleteChatFile,
  getProviders,
  listChats,
  listMessages,
  streamChat,
  streamRepairDeployment,
  uploadChatFile,
  type ChatFileType,
  type ChatType,
  type MessageType,
  type ModelOptionType,
  type OutOfCreditsDetail,
  type ProvidersType,
} from "@/lib/api";
import {
  abortChatStream,
  clearChatStreamSession,
  getChatStreamSnapshot,
  peekPendingRepair,
  setChatStreamAgentStatus,
  setChatStreamMessages,
  startChatTurn,
  startRepairTurn,
  subscribeChatStream,
  takePendingRepair,
  type AgentStatus,
  type ChatMessage,
  type ToolActivityItem,
} from "@/lib/chat-stream-runtime";
import { cn } from "@/lib/cn";

type QueuedMessage = {
  id: string;
  text: string;
  attachments: ChatFileType[];
  provider: string;
  model: string;
};

const PINNED_KEY = "airuntime_pinned_chats";
const PROVIDER_KEY = "airuntime_selected_provider";
const MODEL_KEY = "airuntime_selected_model";

const PROVIDER_LABELS: Record<string, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic Claude",
  gemini: "Gemini",
  openrouter: "OpenRouter",
  routerai: "RouterAI",
};

function readPinned(): string[] {
  if (typeof window === "undefined") return [];
  try {
    return JSON.parse(localStorage.getItem(PINNED_KEY) ?? "[]") as string[];
  } catch {
    return [];
  }
}

function writePinned(ids: string[]) {
  localStorage.setItem(PINNED_KEY, JSON.stringify(ids));
}

function readSelectedProvider(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(PROVIDER_KEY) ?? "";
}

function writeSelectedProvider(value: string) {
  if (typeof window === "undefined") return;
  if (value) localStorage.setItem(PROVIDER_KEY, value);
  else localStorage.removeItem(PROVIDER_KEY);
}

function readSelectedModel(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(MODEL_KEY) ?? "";
}

function writeSelectedModel(value: string) {
  if (typeof window === "undefined") return;
  if (value) localStorage.setItem(MODEL_KEY, value);
  else localStorage.removeItem(MODEL_KEY);
}

function modelPriceLabel(model: ModelOptionType, creditsPerRub = 100): string {
  if (model.input_credits_per_million === null || model.output_credits_per_million === null) {
    return "Стоимость зависит от провайдера";
  }
  const inputRub = model.input_credits_per_million / creditsPerRub;
  const outputRub = model.output_credits_per_million / creditsPerRub;
  return `${inputRub.toLocaleString("ru-RU")} ₽ вход · ${outputRub.toLocaleString("ru-RU")} ₽ выход / 1 млн токенов`;
}

export default function ProjectChatPage() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const router = useRouter();
  const projectId = params.id;
  const [chats, setChats] = useState<ChatType[]>([]);
  const [chatId, setChatId] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [search, setSearch] = useState("");
  const [pinned, setPinned] = useState<string[]>(() => readPinned());
  const [pendingFiles, setPendingFiles] = useState<ChatFileType[]>([]);
  const [uploading, setUploading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [queue, setQueue] = useState<QueuedMessage[]>([]);
  const [agentStatus, setAgentStatus] = useState<AgentStatus | null>(null);
  const [turnStartedAt, setTurnStartedAt] = useState<number | null>(null);
  const [bootstrapping, setBootstrapping] = useState(true);
  const [bootstrapError, setBootstrapError] = useState("");
  const [chatError, setChatError] = useState("");
  const [outOfCredits, setOutOfCredits] = useState<OutOfCreditsDetail | null>(null);
  const [mobilePanel, setMobilePanel] = useState<"list" | "chat">("list");
  const [toolActivity, setToolActivity] = useState<ToolActivityItem[]>([]);
  const [providers, setProviders] = useState<ProvidersType | null>(null);
  const [selectedProvider, setSelectedProvider] = useState<string>(() => readSelectedProvider());
  const [selectedModel, setSelectedModel] = useState<string>(() => readSelectedModel());
  const [providerMenuOpen, setProviderMenuOpen] = useState(false);
  const [stickToBottom, setStickToBottom] = useState(true);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const providerMenuRef = useRef<HTMLDivElement>(null);
  const repairStartedRef = useRef(false);
  const messagesRef = useRef<ChatMessage[]>([]);

  const syncFromRuntime = useCallback(() => {
    if (!projectId || !chatId) return;
    const snap = getChatStreamSnapshot(projectId, chatId);
    setLoading(snap.loading);
    setAgentStatus(snap.agentStatus);
    setTurnStartedAt(snap.turnStartedAt);
    setToolActivity(snap.toolActivity);
    setChatError(snap.chatError);
    setOutOfCredits(snap.outOfCredits ?? null);
    if (snap.messages) {
      messagesRef.current = snap.messages;
      // Streaming text is high-frequency - keep the input/sidebar responsive while the list catches up.
      startTransition(() => {
        setMessages(snap.messages ?? []);
      });
    }
  }, [projectId, chatId]);

  const updateAgentStatus = useCallback(
    (status: AgentStatus | null) => {
      if (projectId && chatId) {
        setChatStreamAgentStatus(projectId, chatId, status);
      }
      setAgentStatus(status);
    },
    [projectId, chatId]
  );

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    if (!projectId || !chatId) return undefined;
    const unsub = subscribeChatStream(projectId, chatId, syncFromRuntime);
    const timer = window.setTimeout(() => syncFromRuntime(), 0);
    return () => {
      window.clearTimeout(timer);
      unsub();
    };
  }, [projectId, chatId, syncFromRuntime]);

  useEffect(() => {
    getProviders()
      .then((data) => {
        setProviders(data);
        if (selectedProvider && !data.configured?.[selectedProvider]) {
          setSelectedProvider("");
          writeSelectedProvider("");
          setSelectedModel("");
          writeSelectedModel("");
          return;
        }
        const configuredModels = data.models?.[selectedProvider] ?? [];
        if (
          selectedProvider &&
          (!selectedModel || !configuredModels.some((model) => model.id === selectedModel))
        ) {
          const fallback = configuredModels[0]?.id ?? data.defaults?.[selectedProvider] ?? "";
          if (fallback) {
            setSelectedModel(fallback);
            writeSelectedModel(fallback);
          }
        }
      })
      .catch(() => setProviders(null));
  }, [selectedModel, selectedProvider]);

  useEffect(() => {
    if (!providerMenuOpen) return undefined;
    const onClickOutside = (event: MouseEvent) => {
      if (providerMenuRef.current && !providerMenuRef.current.contains(event.target as Node)) {
        setProviderMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [providerMenuOpen]);

  useEffect(() => {
    const bootstrap = async () => {
      if (!projectId) return;
      setBootstrapping(true);
      setBootstrapError("");
      try {
        let rows = await listChats(projectId);
        if (rows.length === 0) {
          const chat = await createChat(projectId);
          rows = [chat];
        }
        setChats(rows);
        setChatId(rows[0].id);
        setMobilePanel("chat");
        setStickToBottom(true);
      } catch (err) {
        setBootstrapError(err instanceof Error ? err.message : "Не удалось открыть чат");
      } finally {
        setBootstrapping(false);
      }
    };
    void bootstrap();
  }, [projectId]);

  useEffect(() => {
    const loadMessages = async () => {
      if (!projectId || !chatId) return;
      setStickToBottom(true);
      const snap = getChatStreamSnapshot(projectId, chatId);
      if (snap.loading && snap.messages) {
        messagesRef.current = snap.messages;
        setMessages(snap.messages);
        setAgentStatus(snap.agentStatus);
        setTurnStartedAt(snap.turnStartedAt);
        setToolActivity(snap.toolActivity);
        setLoading(true);
        return;
      }
      const rows = await listMessages(projectId, chatId);
      if (getChatStreamSnapshot(projectId, chatId).loading) return;
      const mapped = rows.map((row: MessageType) => ({
        role: (row.role === "assistant" ? "assistant" : "user") as "user" | "assistant",
        content: row.content_markdown,
        attachments: row.attachments,
      }));
      messagesRef.current = mapped;
      setMessages(mapped);
      setPendingFiles([]);
      setChatStreamMessages(projectId, chatId, mapped);
      if (!snap.loading) {
        setAgentStatus(snap.agentStatus);
      }
    };
    void loadMessages();
  }, [projectId, chatId]);

  const filteredChats = useMemo(() => {
    const query = search.trim().toLowerCase();
    const sorted = [...chats].sort((a, b) => {
      const aPinned = pinned.includes(a.id);
      const bPinned = pinned.includes(b.id);
      if (aPinned !== bPinned) return aPinned ? -1 : 1;
      return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
    });
    if (!query) return sorted;
    return sorted.filter((chat) => chat.title.toLowerCase().includes(query));
  }, [chats, pinned, search]);

  const onNewChat = async () => {
    if (!projectId) return;
    if (chatId) clearChatStreamSession(projectId, chatId);
    const chat = await createChat(projectId);
    setChats((prev) => [chat, ...prev]);
    setChatId(chat.id);
    setMessages([]);
    messagesRef.current = [];
    setPendingFiles([]);
    updateAgentStatus(null);
    setMobilePanel("chat");
    setStickToBottom(true);
  };

  const togglePin = (id: string) => {
    setPinned((prev) => {
      const next = prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id];
      writePinned(next);
      return next;
    });
  };

  const onPickFiles = () => {
    fileInputRef.current?.click();
  };

  const onFilesSelected = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files;
    if (!files?.length || !projectId || !chatId) return;
    setUploading(true);
    setChatError("");
    try {
      const uploaded: ChatFileType[] = [];
      for (const file of Array.from(files)) {
        uploaded.push(await uploadChatFile(projectId, chatId, file));
      }
      setPendingFiles((prev) => [...prev, ...uploaded]);
    } catch (err) {
      setChatError(err instanceof Error ? err.message : "Не удалось загрузить файл");
    } finally {
      setUploading(false);
      event.target.value = "";
    }
  };

  const removePendingFile = async (file: ChatFileType) => {
    if (!projectId || !chatId) return;
    try {
      await deleteChatFile(projectId, chatId, file.id);
      setPendingFiles((prev) => prev.filter((item) => item.id !== file.id));
    } catch (err) {
      setChatError(err instanceof Error ? err.message : "Не удалось удалить файл");
    }
  };

  const onStop = () => {
    if (!projectId || !chatId) return;
    abortChatStream(projectId, chatId);
  };

  const runTurn = async (
    userMessage: string,
    attachments: ChatFileType[],
    provider = selectedProvider,
    model = selectedModel
  ) => {
    if ((!userMessage.trim() && attachments.length === 0) || !projectId || !chatId) return;
    setChatError("");
    setStickToBottom(true);
    const displayUserContent = (userMessage || "Прикреплены файлы").replace(
      /\b\d{6,}:[A-Za-z0-9_-]{20,}\b/g,
      "[TELEGRAM_BOT_TOKEN]"
    );
    const seed = messagesRef.current;
    await startChatTurn({
      projectId,
      chatId,
      userMessage,
      attachments,
      displayUserContent,
      seedMessages: seed,
      createMessage: () => createMessage(projectId, chatId, userMessage, attachments.map((f) => f.id)),
      streamRequest: (signal) =>
        streamChat(projectId, chatId, userMessage, attachments.map((f) => f.id), {
          provider: provider || undefined,
          model: model || undefined,
          signal,
        }),
    });
  };

  const runRepairTurn = async (errorLog?: string | null) => {
    if (!projectId || !chatId || loading) return;
    setMobilePanel("chat");
    setStickToBottom(true);
    const seed = messagesRef.current;
    const note = errorLog?.trim()
      ? "Проверить и исправить по логам с страницы логов"
      : "Проверить и исправить последний деплой";
    await startRepairTurn({
      projectId,
      chatId,
      userNote: note,
      seedMessages: seed,
      streamRequest: (signal) =>
        streamRepairDeployment(projectId, chatId, {
          signal,
          errorLog: errorLog?.trim() || null,
        }),
    });
  };

  useEffect(() => {
    if (bootstrapping || loading || !chatId || !projectId) return;
    const fromUrl = searchParams.get("repair") === "1";
    const pending = peekPendingRepair(projectId);
    if (!fromUrl && !pending) return;
    if (repairStartedRef.current) return;
    repairStartedRef.current = true;
    const errorLog = takePendingRepair(projectId);
    if (fromUrl) {
      router.replace(`/app/projects/${projectId}/chat`, { scroll: false });
    }
    void runRepairTurn(errorLog);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bootstrapping, chatId, loading, projectId, searchParams]);

  useEffect(() => {
    if (loading || queue.length === 0) return;
    const [next, ...rest] = queue;
    const timer = window.setTimeout(() => {
      setQueue(rest);
      void runTurn(next.text, next.attachments, next.provider, next.model);
    }, 0);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, queue]);

  const onSubmit = async (event?: { preventDefault?: () => void }) => {
    event?.preventDefault?.();
    if ((!input.trim() && pendingFiles.length === 0) || !chatId) return;
    const userMessage = input;
    const attachments = pendingFiles;
    setInput("");
    setPendingFiles([]);
    if (loading) {
      setQueue((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          text: userMessage,
          attachments,
          provider: selectedProvider,
          model: selectedModel,
        },
      ]);
      return;
    }
    await runTurn(userMessage, attachments);
  };

  const onRemoveQueued = (id: string) => {
    setQueue((prev) => prev.filter((item) => item.id !== id));
  };

  const onEditQueued = (id: string) => {
    setQueue((prev) => {
      const item = prev.find((row) => row.id === id);
      if (item) {
        setInput(item.text);
        setPendingFiles(item.attachments);
        setSelectedProvider(item.provider);
        writeSelectedProvider(item.provider);
        setSelectedModel(item.model);
        writeSelectedModel(item.model);
      }
      return prev.filter((row) => row.id !== id);
    });
  };

  const canSend = Boolean(chatId) && (input.trim().length > 0 || pendingFiles.length > 0);
  const selectedModelOption = providers?.models?.[selectedProvider]?.find(
    (option) => option.id === selectedModel
  );
  const modelButtonLabel =
    selectedProvider && selectedModel
      ? (selectedModelOption?.label ?? selectedModel)
      : "Авто";
  const currentTitle = filteredChats.find((chat) => chat.id === chatId)?.title ?? "Диалог";
  const streamingMessage = messages[messages.length - 1];
  const liveStreamChars =
    loading && streamingMessage?.role === "assistant" ? streamingMessage.content.length : 0;
  // One chat and nothing to search - the sidebar is just noise next to an empty first message.
  const showChatSidebar =
    mobilePanel === "list" || filteredChats.length > 1 || search.trim().length > 0;

  const onPickPrompt = (prompt: string) => {
    if (!chatId || bootstrapping) {
      setInput(prompt);
      return;
    }
    if (loading) {
      setInput(prompt);
      return;
    }
    setInput("");
    void runTurn(prompt, []);
  };

  return (
    <div
      className={cn(
        "grid min-h-0 flex-1 gap-0 overflow-hidden rounded-[0.95rem] border border-black/[0.07] bg-white shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_32px_-18px_rgba(15,23,42,0.24)]",
        showChatSidebar ? "lg:grid-cols-[220px_1fr]" : "lg:grid-cols-1"
      )}
    >
      <aside
        className={cn(
          "flex min-h-0 flex-col border-black/[0.08] bg-[#f7f8fa] lg:border-r",
          showChatSidebar ? (mobilePanel === "chat" ? "hidden lg:flex" : "flex") : "hidden"
        )}
      >
        <div className="flex items-center justify-between border-b border-black/8 px-3 py-3">
          <p className="text-sm font-medium text-[var(--ar-black)]">Чаты</p>
          <button
            type="button"
            onClick={() => void onNewChat()}
            className="inline-flex h-10 w-10 items-center justify-center rounded-[0.5rem] text-[var(--ar-stone)] hover:bg-black/5 hover:text-[var(--ar-black)]"
            aria-label="Новый чат"
          >
            <Plus size={16} aria-hidden />
          </button>
        </div>

        <div className="relative border-b border-black/8 px-3 py-2">
          <Search size={14} className="absolute left-6 top-1/2 -translate-y-1/2 text-[var(--ar-stone)]" aria-hidden />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Поиск"
            className="w-full rounded-[0.5rem] border-0 bg-white py-2 pl-8 pr-3 text-sm outline-none ring-1 ring-black/10 placeholder:text-[var(--ar-stone)] focus:ring-black/20"
          />
        </div>

        <div className="flex-1 overflow-y-auto p-2">
          {filteredChats.map((chat) => (
            <div
              key={chat.id}
              className={cn(
                "mb-0.5 flex items-center gap-0.5 rounded-[var(--ar-radius-sm)] px-2 py-1.5",
                chat.id === chatId ? "bg-white shadow-sm ring-1 ring-black/8" : "hover:bg-white/70"
              )}
            >
              <button
                type="button"
                className="min-w-0 flex-1 truncate text-left text-sm"
                onClick={() => {
                  setChatId(chat.id);
                  setMobilePanel("chat");
                  setStickToBottom(true);
                }}
              >
                {chat.title || "Без названия"}
              </button>
              <button
                type="button"
                className="inline-flex h-9 w-9 items-center justify-center rounded-[0.5rem] text-[var(--ar-stone)] hover:bg-black/5"
                onClick={() => togglePin(chat.id)}
                aria-label={pinned.includes(chat.id) ? "Открепить" : "Закрепить"}
              >
                {pinned.includes(chat.id) ? <PinOff size={14} aria-hidden /> : <Pin size={14} aria-hidden />}
              </button>
            </div>
          ))}
        </div>
      </aside>

      <section
        className={cn(
          "flex h-full min-h-0 flex-col overflow-hidden",
          mobilePanel === "list" ? "hidden lg:flex" : "flex"
        )}
      >
        <div className="flex items-center gap-2 border-b border-black/8 px-3 py-2.5 lg:px-4">
          <button
            type="button"
            className="inline-flex h-10 w-10 items-center justify-center rounded-[0.5rem] text-[var(--ar-stone)] hover:bg-black/5 lg:hidden"
            onClick={() => setMobilePanel("list")}
            aria-label="К списку чатов"
          >
            <ArrowLeft size={16} aria-hidden />
          </button>
          <p className="min-w-0 flex-1 truncate text-sm font-medium">{currentTitle}</p>
          {!showChatSidebar ? (
            <button
              type="button"
              onClick={() => void onNewChat()}
              className="inline-flex h-10 w-10 items-center justify-center rounded-[0.5rem] text-[var(--ar-stone)] hover:bg-black/5 hover:text-[var(--ar-black)]"
              aria-label="Новый чат"
            >
              <Plus size={16} aria-hidden />
            </button>
          ) : null}
        </div>

        <MessageList
          messages={messages}
          loading={loading}
          stickToBottom={stickToBottom}
          onStickChange={setStickToBottom}
          onPickPrompt={onPickPrompt}
          bottomSlot={
            <div className="space-y-3 pt-2">
              {loading && toolActivity.length > 0 ? <ToolActivityFeed items={toolActivity} /> : null}
              {agentStatus && projectId ? (
                <AgentStatusPanel
                  status={agentStatus}
                  projectId={projectId}
                  turnStartedAt={turnStartedAt}
                  liveChars={liveStreamChars}
                  onContinue={() => void runTurn("Настроил секрет, можешь продолжать.", [])}
                />
              ) : null}
            </div>
          }
        />

        <div className="border-t border-black/8 bg-white px-4 py-3 sm:px-6">
          <div className="mx-auto w-full max-w-3xl">
            {pendingFiles.length > 0 ? (
              <div className="mb-2 flex items-center justify-between rounded-t-[var(--ar-radius-md)] border border-b-0 border-black/10 bg-[#f4f4f5] px-3 py-2 text-xs text-[var(--ar-mist)]">
                <span>
                  {pendingFiles.length} {pendingFiles.length === 1 ? "файл" : "файла"}
                </span>
                <button
                  type="button"
                  className="text-[var(--ar-stone)] hover:text-[var(--ar-black)]"
                  onClick={() => void Promise.all(pendingFiles.map((f) => removePendingFile(f)))}
                >
                  Убрать все
                </button>
              </div>
            ) : null}

            {bootstrapError ? (
              <p className="mb-2 rounded-[0.5rem] border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
                {bootstrapError}
              </p>
            ) : null}
            {outOfCredits ? (
              // A bare red string leaves the user stuck - these are the actual ways forward.
              <div className="mb-2 rounded-[0.7rem] border border-amber-500/30 bg-amber-50 px-4 py-3">
                <p className="text-sm font-medium text-amber-900">{outOfCredits.message}</p>
                <p className="mt-1 text-xs leading-relaxed text-amber-800">
                  На своём API-ключе токены оплачивает провайдер — кредиты не списываются.
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {outOfCredits.actions.map((action) => (
                    <Link key={action.id} href={action.href}>
                      <Button variant={action.id === "topup" ? "accent" : "outline"} size="sm">
                        {action.label}
                      </Button>
                    </Link>
                  ))}
                </div>
              </div>
            ) : chatError ? (
              <p className="mb-2 rounded-[0.5rem] border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
                {chatError}
              </p>
            ) : null}

            {queue.length > 0 ? (
              <div className="mb-2 space-y-1.5 rounded-[var(--ar-radius-md)] border border-black/10 bg-[#fafafa] p-2">
                <p className="px-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--ar-stone)]">
                  В очереди — {queue.length}
                </p>
                {queue.map((item, index) => (
                  <div
                    key={item.id}
                    className="flex items-center gap-2 rounded-[0.5rem] border border-black/8 bg-white px-2.5 py-1.5"
                  >
                    <span className="shrink-0 text-xs font-medium text-[var(--ar-stone)]">{index + 1}.</span>
                    <span className="min-w-0 flex-1 truncate text-sm text-[var(--ar-black)]">
                      {item.text || `Файлы: ${item.attachments.length}`}
                    </span>
                    <button
                      type="button"
                      onClick={() => onEditQueued(item.id)}
                      className="shrink-0 rounded-md px-1.5 py-1 text-xs text-[var(--ar-mist)] hover:bg-black/5 hover:text-[var(--ar-black)]"
                    >
                      Изменить
                    </button>
                    <button
                      type="button"
                      onClick={() => onRemoveQueued(item.id)}
                      className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[var(--ar-stone)] hover:bg-black/5 hover:text-rose-600"
                      aria-label="Убрать из очереди"
                    >
                      <X size={14} aria-hidden />
                    </button>
                  </div>
                ))}
              </div>
            ) : null}

            <form
              onSubmit={(event) => void onSubmit(event)}
              className={cn(
                "relative z-30 overflow-visible rounded-[var(--ar-radius-md)] border border-black/12 bg-white transition-colors focus-within:border-[var(--ar-sky)]/40",
                pendingFiles.length > 0 && "rounded-t-none border-t-0"
              )}
            >
              <input ref={fileInputRef} type="file" className="hidden" multiple onChange={(e) => void onFilesSelected(e)} />

              {pendingFiles.length > 0 ? (
                <div className="flex flex-wrap gap-1.5 border-b border-black/8 px-3 py-2">
                  {pendingFiles.map((file) => (
                    <span
                      key={file.id}
                      className="inline-flex items-center gap-1 rounded-md bg-[#f4f4f5] px-2 py-1 text-xs text-[var(--ar-mist)]"
                    >
                      {file.original_filename}
                      <button type="button" onClick={() => void removePendingFile(file)} aria-label="Убрать">
                        <X size={11} aria-hidden />
                      </button>
                    </span>
                  ))}
                </div>
              ) : null}

              <AutoTextarea
                className="min-h-[52px] max-h-48 resize-none border-0 bg-transparent px-4 py-3.5 text-[15px] shadow-none ring-0 placeholder:text-[var(--ar-stone)] focus:border-0 focus:ring-0"
                value={input}
                onChange={(event) => setInput(event.target.value)}
                placeholder={bootstrapping ? "Загрузка..." : "Опишите задачу"}
                disabled={bootstrapping || !chatId}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    if (canSend) void onSubmit(event);
                  }
                }}
              />

              <div className="flex items-center justify-between gap-2 px-3 pb-2.5 pt-0">
                <div className="relative flex min-w-0 items-center gap-1.5" ref={providerMenuRef}>
                  <button
                    type="button"
                    onClick={() => setProviderMenuOpen((prev) => !prev)}
                    className="inline-flex min-h-10 max-w-[12rem] items-center gap-1 truncate rounded-[0.5rem] px-2 text-xs font-medium text-[var(--ar-mist)] hover:bg-black/5 hover:text-[var(--ar-black)]"
                    aria-label="Выбор модели"
                  >
                    {modelButtonLabel}
                    <ChevronDown size={12} className="opacity-50" aria-hidden />
                  </button>
                  {providerMenuOpen ? (
                    <div className="absolute bottom-full left-0 z-20 mb-2 max-h-[28rem] w-[22rem] overflow-y-auto rounded-[var(--ar-radius-md)] border border-black/10 bg-white py-1 shadow-lg">
                      <button
                        type="button"
                        onClick={() => {
                          setSelectedProvider("");
                          writeSelectedProvider("");
                          setSelectedModel("");
                          writeSelectedModel("");
                          setProviderMenuOpen(false);
                        }}
                        className={cn(
                          "flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left text-sm hover:bg-black/5",
                          !selectedProvider && "font-medium text-[var(--ar-black)]"
                        )}
                      >
                        <span>Авто · лучшее качество</span>
                        <span className="text-xs font-normal text-[var(--ar-stone)]">
                          Сейчас: {providers?.auto_model ?? "модель выбирается сервером"}
                        </span>
                      </button>
                      {(providers?.supported ?? []).map((name) => {
                        const configured = providers?.configured?.[name];
                        const models = providers?.models?.[name] ?? [];
                        return (
                          <div
                            key={name}
                            className={cn("border-t border-black/5 py-1", !configured && "opacity-40")}
                          >
                            <p className="px-3 pb-1 pt-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--ar-stone)]">
                              {PROVIDER_LABELS[name] ?? name}
                              {!configured ? " · не подключён" : ""}
                            </p>
                            {models.map((model) => (
                              <button
                                key={`${name}:${model.id}`}
                                type="button"
                                disabled={!configured}
                                onClick={() => {
                                  setSelectedProvider(name);
                                  writeSelectedProvider(name);
                                  setSelectedModel(model.id);
                                  writeSelectedModel(model.id);
                                  setProviderMenuOpen(false);
                                }}
                                className={cn(
                                  "flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left hover:bg-black/5 disabled:cursor-not-allowed",
                                  selectedProvider === name &&
                                    selectedModel === model.id &&
                                    "bg-black/[0.035]"
                                )}
                              >
                                <span className="text-sm font-medium text-[var(--ar-black)]">
                                  {model.label}
                                </span>
                                {model.description ? (
                                  <span className="text-xs text-[var(--ar-mist)]">
                                    {model.description}
                                  </span>
                                ) : null}
                                <span className="text-[10px] text-[var(--ar-stone)]">
                                  {modelPriceLabel(model, providers?.credits_per_rub)}
                                </span>
                              </button>
                            ))}
                          </div>
                        );
                      })}
                    </div>
                  ) : null}
                </div>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={onPickFiles}
                    disabled={uploading || !chatId}
                    className="inline-flex h-10 w-10 items-center justify-center rounded-[0.5rem] text-[var(--ar-stone)] hover:bg-black/5 hover:text-[var(--ar-black)] disabled:opacity-40"
                    aria-label="Прикрепить"
                  >
                    <Paperclip size={18} aria-hidden />
                  </button>
                  {loading ? (
                    <button
                      type="button"
                      onClick={onStop}
                      className="inline-flex h-10 w-10 items-center justify-center rounded-[0.5rem] bg-[var(--ar-black)] text-white hover:bg-black/85"
                      aria-label="Прервать генерацию"
                    >
                      <Square size={13} strokeWidth={2.5} fill="currentColor" aria-hidden />
                    </button>
                  ) : null}
                  <button
                    type="submit"
                    disabled={bootstrapping || uploading || !canSend}
                    className={cn(
                      "inline-flex h-10 w-10 items-center justify-center rounded-[0.5rem] transition-colors",
                      canSend && !bootstrapping
                        ? "bg-[var(--ar-sky)] text-white hover:brightness-105"
                        : "bg-black/10 text-[var(--ar-stone)]"
                    )}
                    aria-label={loading ? "Добавить в очередь" : "Отправить"}
                  >
                    {loading ? (
                      <Plus size={16} strokeWidth={2.5} aria-hidden />
                    ) : (
                      <ArrowUp size={16} strokeWidth={2.5} aria-hidden />
                    )}
                  </button>
                </div>
              </div>
            </form>
          </div>
        </div>
      </section>
    </div>
  );
}
