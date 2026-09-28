"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowUpRight, Bot, Layers, Lock, MessageSquare } from "lucide-react";

import { ProjectConstellation } from "@/components/app/project-constellation";
import { cn } from "@/lib/cn";
import type { ProjectType } from "@/lib/api";
import { projectStatusLabel, projectStatusTone, projectTypeLabel } from "@/lib/project-status";

const BEACON: Record<string, { dot: string; text: string }> = {
  live: { dot: "bg-emerald-500", text: "text-emerald-700" },
  progress: { dot: "bg-sky-500", text: "text-sky-700" },
  warn: { dot: "bg-amber-500", text: "text-amber-700" },
  danger: { dot: "bg-rose-500", text: "text-rose-700" },
  idle: { dot: "bg-[var(--ar-stone)]", text: "text-[var(--ar-stone)]" },
};

/** The address bar of the card: what the project actually is on the internet right now. */
function AddressStrip({ project }: { project: ProjectType }) {
  const isBot = project.type === "telegram_bot";

  if (project.deployment_url) {
    const host = project.deployment_url.replace(/^https?:\/\//, "").replace(/\/$/, "");
    return (
      <span className="flex min-w-0 items-center gap-1.5">
        <Lock size={11} className="shrink-0 text-emerald-600" aria-hidden />
        <span className="truncate font-mono text-[0.78rem] text-[var(--ar-graphite)]">{host}</span>
        <ArrowUpRight size={12} className="shrink-0 text-[var(--ar-stone)]" aria-hidden />
      </span>
    );
  }

  return (
    <span className="flex min-w-0 items-center gap-1.5">
      {isBot ? (
        <Bot size={12} className="shrink-0 text-[var(--ar-stone)]" aria-hidden />
      ) : (
        <Layers size={12} className="shrink-0 text-[var(--ar-stone)]" aria-hidden />
      )}
      <span className="truncate font-mono text-[0.78rem] text-[var(--ar-stone)]">
        {projectTypeLabel(project.type)} · ещё не в сети
      </span>
    </span>
  );
}

export function ProjectCard({ project }: { project: ProjectType }) {
  const router = useRouter();
  const tone = projectStatusTone(project.status);
  const beacon = BEACON[tone] ?? BEACON.idle;
  const href = `/app/projects/${project.id}`;

  return (
    <article
      role="link"
      tabIndex={0}
      onClick={() => router.push(href)}
      onKeyDown={(event) => {
        if (event.key === "Enter") router.push(href);
      }}
      className="sky-card sky-card-hover group flex cursor-pointer flex-col overflow-hidden rounded-[1.1rem] p-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ar-sky)]/35"
    >
      <div className="relative h-[7.5rem] shrink-0 overflow-hidden">
        <ProjectConstellation seed={project.id} />
        <span className="pointer-events-none absolute inset-x-0 bottom-0 h-10 bg-[linear-gradient(180deg,transparent,rgba(7,12,23,0.55))]" />
        <span className="absolute bottom-2.5 left-3 right-3 flex items-center gap-1.5">
          <span
            className={cn(
              "h-1.5 w-1.5 shrink-0 rounded-full",
              beacon.dot,
              (tone === "live" || tone === "progress") && "live-dot"
            )}
            style={{ color: "currentColor" }}
            aria-hidden
          />
          <span className="truncate text-[0.72rem] font-semibold uppercase tracking-[0.12em] text-white/85">
            {projectStatusLabel(project.status)}
          </span>
        </span>
      </div>

      {/* A visible build bar is the whole point of the card for a deploying project. */}
      {tone === "progress" ? (
        <span className="beacon-bar block h-1 shrink-0 bg-sky-100" aria-hidden />
      ) : (
        <span className="block h-1 shrink-0 bg-black/[0.04]" aria-hidden />
      )}

      <div className="flex flex-1 flex-col gap-3 p-4">
        <div className="min-w-0">
          <h3 className="truncate text-[1.02rem] font-semibold tracking-[-0.015em] text-[var(--ar-black)]">
            {project.name}
          </h3>
          <p className="mt-1.5 flex min-w-0">
            <AddressStrip project={project} />
          </p>
        </div>

        <Link
          href={`${href}/chat`}
          onClick={(event) => event.stopPropagation()}
          className="mt-auto inline-flex h-9 items-center justify-center gap-2 rounded-[0.7rem] border border-black/[0.08] bg-white text-sm font-semibold text-[var(--ar-graphite)] transition-colors hover:border-[var(--ar-sky)]/35 hover:bg-[rgba(31,122,239,0.05)] hover:text-[var(--ar-black)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ar-sky)]/35 group-hover:border-[var(--ar-sky)]/35"
        >
          <MessageSquare size={15} aria-hidden />
          Открыть чат
        </Link>
      </div>
    </article>
  );
}
