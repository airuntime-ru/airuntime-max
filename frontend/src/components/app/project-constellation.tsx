import { cn } from "@/lib/cn";

/**
 * Every project gets its own star pattern, derived from its id. Same project always draws the
 * same constellation, so the cards become recognisable at a glance instead of interchangeable
 * rows - and it costs one static inline SVG, no images and no animation.
 */

function hash(value: string): number {
  let h = 2166136261;
  for (let i = 0; i < value.length; i += 1) {
    h = Math.imul(h ^ value.charCodeAt(i), 16777619);
  }
  return h >>> 0;
}

/** mulberry32 - small, fast, fully deterministic from one seed. */
function seededRandom(seed: number): () => number {
  let a = seed;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Curated so a random id can never produce an off-brand colour. */
const NEBULAS: [string, string][] = [
  ["#2388ff", "#7c6cff"],
  ["#19c7d2", "#2388ff"],
  ["#7c6cff", "#b69cff"],
  ["#5ce6b0", "#19c7d2"],
  ["#4f9dff", "#5ce6b0"],
  ["#8b7fd6", "#4f9dff"],
];

const WIDTH = 320;
const HEIGHT = 132;
const STAR_COUNT = 11;

export function ProjectConstellation({
  seed,
  className,
}: {
  seed: string;
  className?: string;
}) {
  const seedValue = hash(seed);
  const random = seededRandom(seedValue);
  const [from, to] = NEBULAS[seedValue % NEBULAS.length];

  const stars = Array.from({ length: STAR_COUNT }, () => ({
    x: 12 + random() * (WIDTH - 24),
    y: 12 + random() * (HEIGHT - 24),
    r: 0.7 + random() * 1.9,
    o: 0.3 + random() * 0.65,
  }));

  // Link the stars left-to-right so the line never doubles back on itself.
  const path = [...stars].sort((a, b) => a.x - b.x).slice(0, 6);
  const gradientId = `nebula-${seedValue.toString(36)}`;

  return (
    <svg
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      preserveAspectRatio="xMidYMid slice"
      className={cn("h-full w-full", className)}
      aria-hidden
    >
      <defs>
        {/* Radial rather than a shape fill: an ellipse leaves a hard limb across the cover,
            a falloff to zero reads as a nebula. */}
        <radialGradient id={gradientId} cx="28%" cy="16%" r="96%">
          <stop offset="0%" stopColor={from} stopOpacity="0.7" />
          <stop offset="48%" stopColor={to} stopOpacity="0.3" />
          <stop offset="100%" stopColor={to} stopOpacity="0" />
        </radialGradient>
        <radialGradient id={`${gradientId}-b`} cx="82%" cy="88%" r="70%">
          <stop offset="0%" stopColor={to} stopOpacity="0.3" />
          <stop offset="100%" stopColor={to} stopOpacity="0" />
        </radialGradient>
      </defs>

      <rect width={WIDTH} height={HEIGHT} fill="#070c17" />
      <rect width={WIDTH} height={HEIGHT} fill={`url(#${gradientId})`} />
      <rect width={WIDTH} height={HEIGHT} fill={`url(#${gradientId}-b)`} />

      <polyline
        points={path.map((star) => `${star.x.toFixed(1)},${star.y.toFixed(1)}`).join(" ")}
        fill="none"
        stroke="#ffffff"
        strokeOpacity="0.22"
        strokeWidth="0.8"
      />

      {stars.map((star, index) => (
        <circle
          key={index}
          cx={star.x.toFixed(1)}
          cy={star.y.toFixed(1)}
          r={star.r.toFixed(2)}
          fill="#ffffff"
          opacity={star.o.toFixed(2)}
        />
      ))}
    </svg>
  );
}
