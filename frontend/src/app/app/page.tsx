"use client";

import { useEffect, useState } from "react";
import { Plus } from "lucide-react";

import { useCreateProject } from "@/components/app/create-project-context";
import { ProjectCard } from "@/components/app/project-card";
import { Button } from "@/components/ui/button";
import { getProjectRuntimeLimits, type ProjectRuntimeLimitsType } from "@/lib/api";
import { useProjects } from "@/lib/use-projects";

const STARTER_IDEAS = [
  "Сайт автосервиса с записью",
  "Лендинг для запуска курса",
  "Бот принимает заявки и напоминает",
  "Сервис записи к мастеру",
] as const;

// "4 проекта" / "1 проект" / "5 проектов"
function pluralize(count: number, one: string, few: string, many: string): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

export default function ProjectsPage() {
  const { projects, total, deployedTotal, error, loading, loadingMore, hasMore, loadMore } =
    useProjects();
  const { openCreateProject } = useCreateProject();
  const [limits, setLimits] = useState<ProjectRuntimeLimitsType | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        setLimits(await getProjectRuntimeLimits());
      } catch {
        setLimits(null);
      }
    })();
  }, []);

  const running =
    limits?.running ??
    projects.filter((project) => project.status === "live" || project.status === "deploying").length;

  const summary = [
    `${total} ${pluralize(total, "проект", "проекта", "проектов")}`,
    `${running} ${pluralize(running, "запущен", "запущено", "запущено")}`,
    `${deployedTotal} ${pluralize(deployedTotal, "опубликован", "опубликовано", "опубликовано")}`,
  ].join(" · ");

  return (
    <div className="space-y-7">
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
        <div>
          <h1 className="text-[1.9rem] font-semibold leading-[1.1] tracking-[-0.03em] text-[var(--ar-black)] sm:text-[2.35rem]">
            Что <span className="text-daylight">запустим</span> сегодня?
          </h1>
          <p className="mt-2 text-sm text-[var(--ar-mist)]">
            {loading ? "Загружаем проекты…" : summary}
          </p>
        </div>
        <Button
          variant="accent"
          size="lg"
          data-tour="project-create-trigger"
          onClick={() => openCreateProject()}
          className="rounded-full"
        >
          <Plus size={18} />
          Новый проект
        </Button>
      </header>

      {error ? <p className="text-sm text-rose-600">{error}</p> : null}

      {!loading && projects.length === 0 ? (
        // The empty cabinet is the one place that still gets the full deep-space treatment -
        // there is no project content to carry the identity yet.
        <section className="cosmos cosmos-stars cosmos-stars-still relative isolate overflow-hidden rounded-[1.25rem] border border-white/10 px-6 py-14 text-center sm:py-16">
          <div
            className="pointer-events-none absolute left-1/2 top-0 -z-10 h-72 w-72 -translate-x-1/2 -translate-y-1/3 rounded-full bg-[radial-gradient(circle,rgba(35,136,255,0.45),transparent_66%)] blur-3xl"
            aria-hidden
          />
          <h2 className="text-2xl font-semibold tracking-[-0.025em] text-white sm:text-[1.75rem]">
            Здесь появятся ваши проекты
          </h2>
          <p className="mx-auto mt-3 max-w-md text-[0.95rem] leading-relaxed text-white/55">
            Создайте первый — сразу откроется чат, где можно описать задачу обычными словами.
          </p>

          {/* Clickable, not decorative: a first-time user's real blocker is not knowing what
              to type, so each example opens the create dialog. */}
          <p className="mt-9 text-[0.68rem] font-semibold uppercase tracking-[0.16em] text-white/45">
            Например
          </p>
          <ul className="mx-auto mt-3.5 flex max-w-2xl flex-wrap justify-center gap-2">
            {STARTER_IDEAS.map((idea) => (
              <li key={idea}>
                <button
                  type="button"
                  onClick={() => openCreateProject()}
                  className="rounded-full border border-white/12 bg-white/[0.06] px-4 py-2 text-sm text-white/75 transition-colors hover:border-white/30 hover:bg-white/[0.12] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40"
                >
                  {idea}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <div
        className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3"
        data-tour="project-list"
      >
        {projects.map((project) => (
          <ProjectCard key={project.id} project={project} />
        ))}
      </div>

      {hasMore ? (
        <div className="flex justify-center">
          <Button
            variant="outline"
            className="rounded-full"
            onClick={() => void loadMore()}
            disabled={loadingMore}
          >
            {loadingMore ? "Загружаем…" : "Показать ещё"}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
