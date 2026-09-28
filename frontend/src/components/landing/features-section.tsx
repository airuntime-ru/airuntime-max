import { Bot, CloudCog, History, KeyRound, MonitorCheck, Workflow } from "lucide-react";

import { Card } from "@/components/ui/card";

const features = [
  {
    title: "Разработка в диалоге",
    detail: "Пишите задачу обычным языком, прикладывайте файлы и уточняйте результат без потери контекста.",
    icon: Bot,
  },
  {
    title: "Автозапуск",
    detail: "Проект проходит путь до рабочей ссылки: сборка, контейнер, домен и деплой.",
    icon: CloudCog,
  },
  {
    title: "Секреты отдельно",
    detail: "API-ключи и токены хранятся как инфраструктурные секреты, а не в тексте промптов.",
    icon: KeyRound,
  },
  {
    title: "История изменений",
    detail: "Видно, что было запущено, какие шаги прошли и где нужно вмешаться.",
    icon: History,
  },
  {
    title: "Мониторинг статусов",
    detail: "ЛК показывает состояние проекта, деплоя и готовность к публикации.",
    icon: MonitorCheck,
  },
  {
    title: "Масштабируемый процесс",
    detail: "Один и тот же сценарий подходит для лендингов, ботов, MVP и внутренних инструментов.",
    icon: Workflow,
  },
];

export function FeaturesSection() {
  return (
    <section className="py-10 sm:py-16">
      <div className="mb-8 max-w-2xl">
        <p className="text-sm font-semibold uppercase tracking-[0.22em] text-[var(--ar-cyan)]">Возможности</p>
        <h2 className="mt-3 text-3xl font-semibold tracking-tight text-[var(--ar-black)] sm:text-4xl">
          Все важное для первого запуска уже внутри
        </h2>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {features.map((feature) => (
          <Card key={feature.title} hover={false}>
            <div className="flex h-10 w-10 items-center justify-center rounded-[var(--ar-radius-sm)] bg-sky-50 text-[var(--ar-sky)]">
              <feature.icon size={19} />
            </div>
            <p className="mt-5 text-lg font-semibold text-[var(--ar-black)]">{feature.title}</p>
            <p className="mt-2 text-sm leading-relaxed text-[var(--ar-mist)]">{feature.detail}</p>
          </Card>
        ))}
      </div>
    </section>
  );
}
