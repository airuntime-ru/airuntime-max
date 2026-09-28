const lines = [
  "AIRuntime не просит вас разбираться в серверах, деплое и очередях задач.",
  "Вы думаете о продукте. Система ведет проект от идеи до рабочей ссылки.",
  "А когда нужно улучшить результат, вы продолжаете разговор там же.",
];

export function AirSection() {
  return (
    <section className="relative py-12 sm:py-16 md:py-24">
      <div className="mx-auto max-w-4xl space-y-8 text-center sm:space-y-10">
        {lines.map((line) => (
          <p
            key={line}
            className="text-2xl font-light leading-relaxed text-[var(--ar-graphite)] sm:text-3xl md:text-5xl md:leading-tight"
          >
            {line}
          </p>
        ))}
      </div>
    </section>
  );
}
