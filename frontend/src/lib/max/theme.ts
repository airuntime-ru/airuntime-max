import type { CSSProperties } from "react";

import type { ServiceConfig } from "@/lib/max/api";

export type StorefrontMood = ServiceConfig["mood"];

function blobOf(config: ServiceConfig): string {
  return [config.title, config.tagline, config.about, ...config.items.map((item) => item.title)]
    .join(" ")
    .toLowerCase();
}

/**
 * Existing storefronts were generated before mood/comment_hint existed. Infer from the
 * copy so a tutor page stops looking (and asking) like the auto-shop demo without a regen.
 */
export function storefrontMood(config: ServiceConfig): StorefrontMood {
  if (config.mood && config.mood !== "bold") return config.mood;
  const blob = blobOf(config);
  if (/репетитор|урок|егэ|огэ|занят|математик|английск|физик|школ|подготовк/.test(blob)) {
    return "calm";
  }
  if (/кафе|кофе|пекарн|ресторан|пицц|суши|десерт|кондитер/.test(blob)) return "warm";
  if (/консульт|юрист|бухгалтер|агентств|презентац/.test(blob)) return "minimal";
  if (/авто|машин|барбер|стрижк|ремонт|шиномонт/.test(blob)) return "bold";
  return config.mood || "bold";
}

export function commentHint(config: ServiceConfig): string {
  const written = (config.comment_hint || "").trim();
  if (written && !/марка авто/i.test(written)) return written;
  const blob = blobOf(config);
  if (/репетитор|урок|егэ|огэ|занят|математик|английск|физик|школ/.test(blob)) {
    return "Класс, тема занятия, онлайн или очно";
  }
  if (/авто|машин|шиномонт|двигател|подвеск/.test(blob)) {
    return "Марка, год, что случилось";
  }
  if (/кафе|кофе|пекарн|ресторан|пицц|суши/.test(blob)) {
    return "Аллергии, пожелания к заказу";
  }
  if (config.kind === "menu") return "Аллергии, пожелания к заказу";
  if (config.kind === "landing") return "Что нужно обсудить";
  return written || "Пожелания к записи";
}

export function kindKicker(kind: ServiceConfig["kind"]): string {
  if (kind === "menu") return "Меню";
  if (kind === "landing") return "Заявка";
  return "Запись";
}

function luminance(hex: string): number | null {
  const raw = hex.replace("#", "");
  const normalized = raw.length === 3 ? raw.split("").map((part) => part + part).join("") : raw;
  if (!/^[0-9a-f]{6}$/i.test(normalized)) return null;
  const channels = [0, 2, 4].map(
    (offset) => parseInt(normalized.slice(offset, offset + 2), 16) / 255
  );
  const linear = channels.map((channel) =>
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  );
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrast(first: string, second: string): number {
  const a = luminance(first);
  const b = luminance(second);
  if (a === null || b === null) return 1;
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

export function readableAccentText(hex: string): "#ffffff" | "#10141c" {
  const value = luminance(hex);
  if (value === null) return "#ffffff";
  // 0.179 is the WCAG crossover where black starts having the higher contrast ratio.
  return value > 0.179 ? "#10141c" : "#ffffff";
}

type Palette = { bg: string; surface: string; ink: string };

// The presets storefronts used before the model could choose its own palette. They stay
// the fallback, so a config without `palette` renders exactly as it was approved.
const PRESETS: Record<"light" | "dark", Record<StorefrontMood, Palette>> = {
  light: {
    bold: { bg: "#f2f4f7", surface: "#ffffff", ink: "#10141c" },
    calm: { bg: "#f3efe6", surface: "#fffcf6", ink: "#1b1712" },
    warm: { bg: "#f7eee6", surface: "#fffaf5", ink: "#1d140e" },
    minimal: { bg: "#f6f6f4", surface: "#ffffff", ink: "#111214" },
  },
  dark: {
    bold: { bg: "#0d1015", surface: "#171b22", ink: "#f4f6f9" },
    calm: { bg: "#12130f", surface: "#1c1e18", ink: "#f3f0e6" },
    warm: { bg: "#160f0b", surface: "#241a14", ink: "#fff5eb" },
    minimal: { bg: "#101112", surface: "#191a1c", ink: "#f4f4f2" },
  },
};

const DEFAULT_TONE: Record<StorefrontMood, NonNullable<ServiceConfig["hero_tone"]>> = {
  bold: "accent",
  warm: "accent",
  calm: "tint",
  minimal: "plain",
};

const LEGACY_FONT: Record<string, string> = {
  sans: "inter",
  serif: "playfair",
  display: "unbounded",
};

export type StorefrontDesign = {
  attrs: Record<string, string>;
  style: CSSProperties;
};

/**
 * Everything visual about one storefront, resolved once: data attributes for the CSS
 * composition primitives and the colour variables they read.
 *
 * Contrast is decided here, not in CSS, because only here are the actual colours known:
 * a yellow accent is a fine button and an unreadable price, and the stylesheet cannot
 * tell those two cases apart.
 */
export function storefrontDesign(config: ServiceConfig): StorefrontDesign {
  const mood = storefrontMood(config);
  const chosen = config.palette;
  const hasPalette = Boolean(chosen?.bg && chosen?.ink);
  const scheme: "light" | "dark" = hasPalette
    ? (luminance(chosen!.bg) ?? 1) < 0.2
      ? "dark"
      : "light"
    : config.color_scheme === "dark"
      ? "dark"
      : "light";
  const preset = PRESETS[scheme][mood];
  const bg = hasPalette ? chosen!.bg : preset.bg;
  const ink = hasPalette ? chosen!.ink : preset.ink;
  const surface =
    hasPalette && chosen!.surface && contrast(chosen!.surface, ink) >= 6
      ? chosen!.surface
      : hasPalette
        ? `color-mix(in srgb, ${ink} ${scheme === "dark" ? 7 : 3}%, ${bg})`
        : preset.surface;
  const accent = config.accent || "#2e7cf6";
  const onAccent = readableAccentText(accent);
  // A second colour for gradients and decoration. It must sit on the same side of the
  // light/dark line as the accent, or the hero text would be readable on only half of it.
  const accent2 =
    chosen?.accent2 && readableAccentText(chosen.accent2) === onAccent
      ? chosen.accent2
      : `color-mix(in srgb, ${accent} 62%, ${onAccent === "#ffffff" ? "#000000" : "#ffffff"})`;
  // Accent used as text (prices, active tab) has to read on the page, not just on itself.
  const accentInk = contrast(accent, bg) >= 3.2 ? accent : ink;

  const tone = config.hero_tone || DEFAULT_TONE[mood];
  const heroBg =
    tone === "accent"
      ? `linear-gradient(140deg, ${accent} 8%, ${accent2})`
      : tone === "ink"
        ? ink
        : tone === "tint"
          ? `color-mix(in srgb, ${accent} 16%, ${surface})`
          : surface;
  const heroInk = tone === "accent" ? onAccent : tone === "ink" ? bg : ink;

  const font = config.heading_font || LEGACY_FONT[config.heading_style] || "inter";
  // Big display headlines must never break inside a word ("банкротств-у"). CSS cannot
  // measure a word, so the longest one is counted here and the size capped from it.
  const longestWord = Math.max(4, ...config.title.split(/\s+/).map((word) => word.length));
  const hasPhotos = Boolean(config.hero_image) || config.items.some((item) => item.image_url);

  return {
    attrs: {
      "data-scheme": scheme,
      "data-font": font,
      "data-body": font === "inter" ? "inter" : "manrope",
      "data-layout": config.layout || "classic",
      "data-heading": config.heading_style || "sans",
      "data-nav": config.nav_style || "none",
      "data-hero": config.hero_style || "split",
      "data-tone": tone,
      "data-pattern": config.pattern || "none",
      "data-card": config.card_style || "minimal",
      "data-radius": config.radius_style || "soft",
      "data-density": config.density || "balanced",
      "data-photos": hasPhotos ? "yes" : "no",
    },
    style: {
      "--max-bg": bg,
      "--max-sheet": surface,
      "--max-ink": ink,
      "--max-muted": `color-mix(in srgb, ${ink} 62%, ${bg})`,
      "--max-line": `color-mix(in srgb, ${ink} ${scheme === "dark" ? 14 : 10}%, transparent)`,
      "--max-accent": accent,
      "--max-accent-2": accent2,
      "--max-accent-ink": accentInk,
      "--max-on-accent": onAccent,
      "--max-hero-bg": heroBg,
      "--max-hero-ink": heroInk,
      "--max-title-chars": longestWord,
      colorScheme: scheme,
    } as CSSProperties,
  };
}
