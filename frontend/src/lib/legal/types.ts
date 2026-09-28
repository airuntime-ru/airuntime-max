export type LegalSection = {
  id: string;
  title: string;
  paragraphs: string[];
  /** Optional nested numbered/bulleted items under a paragraph index */
  lists?: Record<number, string[]>;
};

export type LegalDocument = {
  slug: "offer" | "privacy" | "cookies";
  title: string;
  shortTitle: string;
  description: string;
  updatedAt: string;
  sections: LegalSection[];
};
