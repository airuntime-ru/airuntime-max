import { Reveal } from "@/components/ui/reveal";
import { cn } from "@/lib/cn";

type SectionHeadingProps = {
  eyebrow?: string;
  title: React.ReactNode;
  description?: string;
  align?: "left" | "center";
  tone?: "light" | "dark";
  className?: string;
  titleAs?: "h2" | "h3";
  id?: string;
};

export function SectionHeading({
  eyebrow,
  title,
  description,
  align = "left",
  tone = "light",
  className,
  titleAs: TitleTag = "h2",
  id,
}: SectionHeadingProps) {
  const dark = tone === "dark";

  return (
    <div
      className={cn(
        "max-w-2xl 2xl:max-w-3xl",
        align === "center" && "mx-auto text-center 2xl:max-w-4xl 3xl:max-w-5xl",
        className
      )}
    >
      {eyebrow ? (
        <Reveal>
          <p
            className={cn(
              "mb-4 inline-flex items-center gap-2 text-[0.78rem] font-semibold uppercase tracking-[0.2em] 2xl:text-[0.84rem]",
              dark ? "text-[#7fb6ff]" : "text-[var(--ar-sky)]"
            )}
          >
            <span
              className="h-1 w-6 rounded-full bg-[image:var(--ar-accent-gradient)]"
              aria-hidden
            />
            {eyebrow}
          </p>
        </Reveal>
      ) : null}
      <Reveal delay={eyebrow ? 60 : 0}>
        <TitleTag
          id={id}
          className={cn(
            "text-balance text-[2.15rem] font-semibold leading-[1.1] tracking-[-0.035em] sm:text-[2.75rem] lg:text-[3.15rem] 2xl:text-[3.5rem] 3xl:text-[3.9rem]",
            dark ? "text-white" : "text-[var(--ar-black)]"
          )}
        >
          {title}
        </TitleTag>
      </Reveal>
      {description ? (
        <Reveal delay={120}>
          <p
            className={cn(
              "mt-5 max-w-xl text-lg leading-relaxed 2xl:max-w-2xl 2xl:text-xl",
              align === "center" && "mx-auto text-balance",
              dark ? "text-white/55" : "text-[var(--ar-mist)]"
            )}
          >
            {description}
          </p>
        </Reveal>
      ) : null}
    </div>
  );
}

type LandingSectionProps = {
  id?: string;
  children: React.ReactNode;
  className?: string;
  tone?: "default" | "muted" | "cosmos";
  ariaLabelledBy?: string;
};

export function LandingSection({
  id,
  children,
  className,
  tone = "default",
  ariaLabelledBy,
}: LandingSectionProps) {
  return (
    <section
      id={id}
      aria-labelledby={ariaLabelledBy}
      className={cn(
        "relative scroll-mt-24 py-20 sm:py-24 lg:py-28 2xl:py-32 3xl:py-40",
        tone === "default" && "bg-white",
        tone === "muted" && "daylight",
        tone === "cosmos" && "cosmos cosmos-stars",
        className
      )}
    >
      <div className="landing-container relative">{children}</div>
    </section>
  );
}
