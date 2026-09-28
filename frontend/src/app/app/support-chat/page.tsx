"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";

import {
  closeStaffConversation,
  fetchStaffConversation,
  fetchStaffConversations,
  markStaffRead,
  openStaffConversationForUser,
  sendStaffMessage,
  setAccessToken,
  type StaffConversationSummaryType,
  type SupportConversationType,
} from "@/lib/api";
import { cn } from "@/lib/cn";

const POLL_MS = 2000;

export default function SupportStaffChatPage() {
  const searchParams = useSearchParams();
  const bridgeToken = searchParams.get("bridge_token");
  const customerUserId = searchParams.get("customer_user_id");

  const [ready, setReady] = useState(!bridgeToken);
  const [inbox, setInbox] = useState<StaffConversationSummaryType[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [thread, setThread] = useState<SupportConversationType | null>(null);
  const [draft, setDraft] = useState("");
  const [filter, setFilter] = useState<"all" | "open" | "closed">("open");
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!bridgeToken) return;
    setAccessToken(bridgeToken);
    const timer = window.setTimeout(() => setReady(true), 0);
    return () => window.clearTimeout(timer);
  }, [bridgeToken]);

  const loadInbox = useCallback(async () => {
    try {
      const data = await fetchStaffConversations({
        status: filter,
        unreadOnly,
      });
      setInbox(data.items);
    } catch {
      // ignore transient poll errors
    }
  }, [filter, unreadOnly]);

  const loadThread = useCallback(async (conversationId: string) => {
    const data = await fetchStaffConversation(conversationId);
    setThread(data);
    const unreadIds = data.messages
      .filter((m) => m.sender_party === "user" && !m.read_at)
      .map((m) => m.id);
    if (unreadIds.length) {
      await markStaffRead(conversationId, unreadIds);
    }
  }, []);

  useEffect(() => {
    if (!ready) return;
    const kickoff = window.setTimeout(() => void loadInbox(), 0);
    const timer = window.setInterval(loadInbox, POLL_MS);
    return () => {
      window.clearTimeout(kickoff);
      window.clearInterval(timer);
    };
  }, [ready, loadInbox]);

  useEffect(() => {
    if (!ready || !customerUserId) return;
    void (async () => {
      const data = await openStaffConversationForUser(customerUserId);
      setActiveId(data.id);
      setThread(data);
    })();
  }, [ready, customerUserId]);

  useEffect(() => {
    if (!activeId) return;
    const kickoff = window.setTimeout(() => void loadThread(activeId), 0);
    const timer = window.setInterval(() => loadThread(activeId), POLL_MS);
    return () => {
      window.clearTimeout(kickoff);
      window.clearInterval(timer);
    };
  }, [activeId, loadThread]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [thread?.messages.length, activeId]);

  const activeSummary = useMemo(
    () => inbox.find((item) => item.id === activeId) ?? null,
    [inbox, activeId]
  );

  const onSend = async () => {
    if (!activeId || !draft.trim() || sending) return;
    setSending(true);
    setSendError(null);
    try {
      const message = await sendStaffMessage(activeId, draft.trim());
      setDraft("");
      setThread((prev) =>
        prev ? { ...prev, messages: [...prev.messages, message] } : prev
      );
      void loadInbox();
    } catch {
      setSendError("Не удалось отправить сообщение. Проверьте авторизацию и попробуйте снова.");
    } finally {
      setSending(false);
    }
  };

  if (!ready) {
    return <p className="p-6 text-sm text-[var(--ar-mist)]">Подключение…</p>;
  }

  return (
    <div className="mx-auto flex min-h-0 w-full max-w-6xl flex-1 flex-col gap-4 lg:flex-row lg:min-h-[calc(100dvh-8rem)]">
      <aside className="flex max-h-[40dvh] w-full shrink-0 flex-col overflow-hidden rounded-2xl border border-black/10 bg-white lg:max-h-none lg:w-80">
        <div className="border-b border-black/5 p-3">
          <h1 className="text-lg font-semibold">Поддержка</h1>
          <div className="mt-2 flex flex-wrap gap-2 text-xs">
            {(["all", "open", "closed"] as const).map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => setFilter(value)}
                className={cn(
                  "rounded-full px-3 py-1",
                  filter === value ? "bg-[var(--ar-black)] text-white" : "bg-black/5"
                )}
              >
                {value === "all" ? "Все" : value === "open" ? "Открытые" : "Закрытые"}
              </button>
            ))}
            <button
              type="button"
              onClick={() => setUnreadOnly((v) => !v)}
              className={cn(
                "rounded-full px-3 py-1",
                unreadOnly ? "bg-red-600 text-white" : "bg-black/5"
              )}
            >
              Непрочитанные
            </button>
          </div>
        </div>
        <ul className="min-h-0 flex-1 overflow-y-auto">
          {inbox.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                onClick={() => setActiveId(item.id)}
                className={cn(
                  "w-full border-b border-black/5 px-3 py-3 text-left hover:bg-black/[0.03]",
                  activeId === item.id && "bg-black/[0.04]"
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-medium">{item.user_email}</span>
                  {item.unread_from_user > 0 ? (
                    <span className="rounded-full bg-red-600 px-2 py-0.5 text-[10px] text-white">
                      {item.unread_from_user}
                    </span>
                  ) : null}
                </div>
                <p className="mt-1 truncate text-xs text-[var(--ar-mist)]">
                  {item.last_message_preview || "—"}
                </p>
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <section className="flex min-h-0 min-h-[50dvh] flex-1 flex-col overflow-hidden rounded-2xl border border-black/10 bg-white lg:min-h-0">
        {activeId && thread ? (
          <>
            <div className="flex shrink-0 items-center justify-between border-b border-black/5 px-4 py-3">
              <div>
                <p className="font-semibold">{activeSummary?.user_email ?? "Диалог"}</p>
                <p className="text-xs text-[var(--ar-mist)]">Статус: {thread.status}</p>
              </div>
              {thread.status === "open" ? (
                <button
                  type="button"
                  className="rounded-full border border-black/10 px-3 py-1 text-xs"
                  onClick={() => void closeStaffConversation(activeId).then(() => loadThread(activeId))}
                >
                  Закрыть тикет
                </button>
              ) : null}
            </div>
            <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4">
              {thread.messages.map((message) => (
                <div
                  key={message.id}
                  className={cn("flex", message.sender_party === "staff" ? "justify-end" : "justify-start")}
                >
                  <div
                    className={cn(
                      "max-w-[85%] rounded-2xl px-3 py-2 text-sm whitespace-pre-wrap break-words",
                      message.sender_party === "staff"
                        ? "bg-[var(--ar-black)] text-white"
                        : "bg-black/[0.06]"
                    )}
                  >
                    {message.body}
                  </div>
                </div>
              ))}
              <div ref={bottomRef} />
            </div>
            <div className="shrink-0 border-t border-black/5 p-3">
              {sendError ? <p className="mb-2 text-xs text-red-600">{sendError}</p> : null}
              <div className="flex gap-2">
                <textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  rows={2}
                  className="min-h-[44px] flex-1 resize-none rounded-xl border border-black/10 px-3 py-2 text-sm outline-none focus:border-[var(--ar-sky)]"
                  placeholder="Ответ пользователю…"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      void onSend();
                    }
                  }}
                />
                <button
                  type="button"
                  disabled={sending || !draft.trim()}
                  onClick={() => void onSend()}
                  className="self-end rounded-xl bg-[var(--ar-black)] px-4 py-2 text-sm text-white disabled:opacity-40"
                >
                  {sending ? "…" : "Отправить"}
                </button>
              </div>
            </div>
          </>
        ) : (
          <p className="p-6 text-sm text-[var(--ar-mist)]">Выберите диалог слева</p>
        )}
      </section>
    </div>
  );
}
