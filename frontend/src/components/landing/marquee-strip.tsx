import { marqueeItems } from "@/lib/landing/content";

/**
 * Ticker of runtime guarantees. The track holds the list twice and slides exactly
 * -50%, so the loop is seamless with a single transform animation.
 */
export function MarqueeStrip() {
  const loop = [...marqueeItems, ...marqueeItems];

  return (
    <section
      className="cosmos relative border-y border-white/8 py-4 sm:py-5 2xl:py-6"
      aria-label="Что делает платформа"
    >
      <div className="marquee" aria-hidden>
        <div className="marquee-track gap-8 sm:gap-12">
          {loop.map((item, index) => (
            <span key={`${item}-${index}`} className="flex shrink-0 items-center gap-8 sm:gap-12">
              <span className="text-[0.95rem] font-medium uppercase tracking-[0.12em] text-white/55 sm:text-base 2xl:text-[1.05rem]">
                {item}
              </span>
              <span className="text-[#5ce6b0]/50" aria-hidden>
                ✦
              </span>
            </span>
          ))}
        </div>
      </div>
      <p className="sr-only">{marqueeItems.join(", ")}</p>
    </section>
  );
}
