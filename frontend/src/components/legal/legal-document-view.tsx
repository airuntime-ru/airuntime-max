import Link from "next/link";

import { Logo } from "@/components/brand/logo";
import { legalLinks, type LegalDocument } from "@/lib/legal";

function formatDate(iso: string): string {
  const date = new Date(`${iso}T00:00:00`);
  return new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(date);
}

export function LegalDocumentView({ document }: { document: LegalDocument }) {
  return (
    <div className="min-h-screen bg-[var(--ar-canvas)] text-[var(--ar-black)]">
      <header className="border-b border-black/8 bg-white/80 backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-4 px-5 py-4 sm:px-8">
          <Logo href="/" variant="full" theme="dark" size="sm" />
          <Link
            href="/"
            className="text-sm text-[var(--ar-mist)] transition-colors hover:text-[var(--ar-black)]"
          >
            На главную
          </Link>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-5 py-10 sm:px-8 sm:py-14">
        <p className="text-sm text-[var(--ar-mist)]">
          Обновлено {formatDate(document.updatedAt)}
        </p>
        <h1 className="mt-3 text-3xl font-semibold tracking-[-0.03em] sm:text-4xl">
          {document.title}
        </h1>
        <p className="mt-4 text-base leading-relaxed text-[var(--ar-mist)]">
          {document.description}
        </p>

        <nav
          aria-label="Юридические документы"
          className="mt-8 flex flex-wrap gap-x-5 gap-y-2 border-b border-black/8 pb-6"
        >
          {legalLinks.map((item) => {
            const active = item.href === `/legal/${document.slug}`;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={
                  active
                    ? "text-sm font-medium text-[var(--ar-sky)]"
                    : "text-sm text-[var(--ar-mist)] hover:text-[var(--ar-black)]"
                }
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="mt-10 space-y-10">
          {document.sections.map((section) => (
            <section key={section.id} id={section.id} className="scroll-mt-24">
              {section.title ? (
                <h2 className="text-xl font-semibold tracking-[-0.02em]">{section.title}</h2>
              ) : null}
              <div className={section.title ? "mt-4 space-y-4" : "space-y-4"}>
                {section.paragraphs.map((paragraph, index) => (
                  <div key={`${section.id}-${index}`}>
                    <p className="text-[0.95rem] leading-7 text-[var(--ar-graphite)]">
                      {paragraph}
                    </p>
                    {section.lists?.[index]?.length ? (
                      <ul className="mt-3 list-disc space-y-2 pl-5 text-[0.95rem] leading-7 text-[var(--ar-graphite)]">
                        {section.lists[index].map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
      </main>
    </div>
  );
}
