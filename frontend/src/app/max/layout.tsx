import type { Metadata } from "next";
import {
  Comfortaa,
  Cormorant_Garamond,
  Lora,
  Manrope,
  Oswald,
  Playfair_Display,
  Rubik,
  Unbounded,
} from "next/font/google";
import Script from "next/script";

import "./max.css";

// Storefront typefaces. Every one carries Cyrillic, and none is preloaded: a storefront
// uses one heading face plus Manrope, and the browser only downloads a face some visible
// text actually uses - so shipping the whole set costs a customer one or two files.
// next/font compiles these calls statically, so every option has to be a literal here.
const manrope = Manrope({
  subsets: ["latin", "cyrillic"],
  display: "swap",
  preload: false,
  variable: "--max-font-manrope",
});
const rubik = Rubik({
  subsets: ["latin", "cyrillic"],
  display: "swap",
  preload: false,
  variable: "--max-font-rubik",
});
const unbounded = Unbounded({
  subsets: ["latin", "cyrillic"],
  display: "swap",
  preload: false,
  variable: "--max-font-unbounded",
});
const oswald = Oswald({
  subsets: ["latin", "cyrillic"],
  display: "swap",
  preload: false,
  variable: "--max-font-oswald",
});
const playfair = Playfair_Display({
  subsets: ["latin", "cyrillic"],
  display: "swap",
  preload: false,
  variable: "--max-font-playfair",
});
const cormorant = Cormorant_Garamond({
  subsets: ["latin", "cyrillic"],
  // A single face is enough for display copy and avoids a Turbopack/next-font failure
  // where a multi-weight Google font is emitted as an invalid multi-entry query.
  weight: "600",
  display: "swap",
  preload: false,
  variable: "--max-font-cormorant",
});
const lora = Lora({
  subsets: ["latin", "cyrillic"],
  display: "swap",
  preload: false,
  variable: "--max-font-lora",
});
const comfortaa = Comfortaa({
  subsets: ["latin", "cyrillic"],
  display: "swap",
  preload: false,
  variable: "--max-font-comfortaa",
});

const fontVariables = [manrope, rubik, unbounded, oswald, playfair, cormorant, lora, comfortaa]
  .map((font) => font.variable)
  .join(" ");

export const metadata: Metadata = {
  title: "AIRuntime для MAX",
  description: "AIRuntime и заявки прямо в мессенджере MAX.",
  // A mini app is opened from inside MAX, never found in search.
  robots: { index: false, follow: false },
};

export default function MaxLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className={`max-app ${fontVariables}`}>
      {/* beforeInteractive so window.WebApp exists before the first client render tries to
          read initData - otherwise every load would flash the "open inside MAX" state. */}
      <Script src="https://st.max.ru/js/max-web-app.js" strategy="beforeInteractive" />
      {children}
    </div>
  );
}
