import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { LegalDocumentView } from "@/components/legal/legal-document-view";
import { getLegalDocument, legalDocuments } from "@/lib/legal";

type PageProps = {
  params: Promise<{ slug: string }>;
};

export function generateStaticParams() {
  return legalDocuments.map((doc) => ({ slug: doc.slug }));
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { slug } = await params;
  const document = getLegalDocument(slug);
  if (!document) {
    return { title: "Документ не найден" };
  }
  return {
    title: document.shortTitle,
    description: document.description,
  };
}

export default async function LegalDocumentPage({ params }: PageProps) {
  const { slug } = await params;
  const document = getLegalDocument(slug);
  if (!document) notFound();
  return <LegalDocumentView document={document} />;
}
