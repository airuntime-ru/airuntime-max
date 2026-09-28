"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { ArrowLeft, Bot, KeyRound, MessageSquare } from "lucide-react";

import { Button } from "@/components/ui/button";

function TelegramTokenHelpContent() {
  const searchParams = useSearchParams();
  const projectId = searchParams.get("projectId");
  const chatHref = projectId ? `/app/projects/${projectId}/chat` : "/app";
  const secretsHref = projectId ? `/app/projects/${projectId}/settings#secrets` : "/app";

  return (
    <main className="min-h-screen bg-[var(--ar-porcelain)] px-5 py-10 text-[var(--ar-black)]">
      <div className="mx-auto max-w-3xl">
        <Link href={projectId ? chatHref : "/app"}>
          <Button variant="ghost" size="sm">
            <ArrowLeft size={16} />
            {projectId ? "Назад в чат" : "AIRuntime"}
          </Button>
        </Link>

        <section className="mt-8 rounded-[var(--ar-radius-sm)] border border-black/10 bg-white p-6 shadow-sm sm:p-8">
          <span className="inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-sky-50 text-[var(--ar-sky)]">
            <Bot size={22} />
          </span>
          <h1 className="mt-5 text-3xl font-semibold tracking-normal sm:text-5xl">
            Как получить Telegram-токен
          </h1>
          <p className="mt-4 text-base leading-8 text-[var(--ar-mist)]">
            Токен нужен AIRuntime, чтобы запустить вашего бота от имени созданного Telegram-бота.
          </p>

          <div className="mt-7 grid gap-3">
            {[
              "Откройте @BotFather в Telegram.",
              "Отправьте команду /newbot.",
              "Введите имя и username бота.",
              "Скопируйте токен, который выдаст BotFather.",
              "В AIRuntime откройте секреты проекта и добавьте TELEGRAM_BOT_TOKEN.",
            ].map((step, index) => (
              <div key={step} className="flex gap-3 rounded-xl border border-black/8 bg-black/[0.02] p-4">
                <span className="text-sm font-semibold text-[var(--ar-sky)]">{index + 1}</span>
                <p className="text-sm leading-6 text-[var(--ar-graphite)]">{step}</p>
              </div>
            ))}
          </div>

          <div className="mt-7 flex flex-col gap-3 sm:flex-row sm:flex-wrap">
            <a href="https://t.me/BotFather" target="_blank" rel="noreferrer">
              <Button variant="accent" className="w-full sm:w-auto">
                <Bot size={16} />
                Открыть BotFather
              </Button>
            </a>
            {projectId ? (
              <>
                <Link href={secretsHref}>
                  <Button variant="outline" className="w-full sm:w-auto">
                    <KeyRound size={16} />
                    В секреты
                  </Button>
                </Link>
                <Link href={chatHref}>
                  <Button variant="outline" className="w-full sm:w-auto">
                    <MessageSquare size={16} />
                    В чат
                  </Button>
                </Link>
              </>
            ) : (
              <Link href="/app">
                <Button variant="outline" className="w-full sm:w-auto">
                  <KeyRound size={16} />
                  В проекты
                </Button>
              </Link>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}

export default function TelegramTokenHelpPage() {
  return (
    <Suspense fallback={<main className="min-h-screen bg-[var(--ar-porcelain)]" />}>
      <TelegramTokenHelpContent />
    </Suspense>
  );
}
