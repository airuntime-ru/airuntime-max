import { cookiesDocument } from "./cookies";
import { offerDocument } from "./offer";
import { privacyDocument } from "./privacy";
import type { LegalDocument } from "./types";

export const legalDocuments: LegalDocument[] = [
  offerDocument,
  privacyDocument,
  cookiesDocument,
];

export const legalLinks = [
  { href: "/legal/offer", label: "Публичная оферта" },
  { href: "/legal/privacy", label: "Политика конфиденциальности" },
  { href: "/legal/cookies", label: "Политика cookie" },
] as const;

export function getLegalDocument(slug: string): LegalDocument | undefined {
  return legalDocuments.find((doc) => doc.slug === slug);
}

export type { LegalDocument, LegalSection } from "./types";
export { operator } from "./operator";
export { offerDocument } from "./offer";
export { privacyDocument } from "./privacy";
export { cookiesDocument } from "./cookies";
