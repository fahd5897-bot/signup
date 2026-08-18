import { getTranslations, setRequestLocale } from "next-intl/server";

import { DocumentTable } from "@/components/features/knowledge-base/document-table";
import { Dropzone } from "@/components/features/upload/dropzone";
import type { DocumentSummary } from "@/lib/api/types";

/**
 * Placeholder corpus.
 *
 * Replaced by `GET /api/v1/documents` once the document service lands
 * (Phase 4 of the backend left it stubbed). Deliberately includes a degraded
 * Arabic scan and a failed upload, because those two states are the ones the
 * layout has to accommodate and the happy path never exercises.
 */
const SAMPLE_DOCUMENTS: DocumentSummary[] = [
  {
    id: "1",
    filename: "ISO 27001 Certificate 2024.pdf",
    mime_type: "application/pdf",
    size_bytes: 482_113,
    role: "knowledge_base",
    status: "ready",
    language: "en",
    page_count: 2,
    chunk_count: 4,
    text_extraction_ratio: 1,
    extraction_quality: "good",
    failure_reason: null,
    created_at: "2026-08-02T09:12:00Z",
  },
  {
    id: "2",
    filename: "كراسة الشروط والمواصفات ٢٠٢٥.pdf",
    mime_type: "application/pdf",
    size_bytes: 18_224_640,
    role: "knowledge_base",
    status: "ready",
    language: "ar",
    page_count: 214,
    chunk_count: 1_038,
    text_extraction_ratio: 0.72,
    extraction_quality: "degraded",
    failure_reason: null,
    created_at: "2026-08-05T14:31:00Z",
  },
  {
    id: "3",
    filename: "Bill of Quantities — Riyadh Metro.xlsx",
    mime_type:
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    size_bytes: 96_204,
    role: "knowledge_base",
    status: "embedding",
    language: "mixed",
    page_count: 12,
    chunk_count: 0,
    text_extraction_ratio: null,
    extraction_quality: "unknown",
    failure_reason: null,
    created_at: "2026-08-17T11:02:00Z",
  },
  {
    id: "4",
    filename: "Scanned Reference Letter.pdf",
    mime_type: "application/pdf",
    size_bytes: 3_401_221,
    role: "knowledge_base",
    status: "failed",
    language: "unknown",
    page_count: 3,
    chunk_count: 0,
    text_extraction_ratio: 0.04,
    extraction_quality: "poor",
    failure_reason:
      "No readable text could be extracted. If this is a scanned document, please upload a higher-quality scan.",
    created_at: "2026-08-16T08:45:00Z",
  },
];

export default async function KnowledgeBasePage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations("knowledgeBase");

  return (
    <div className="h-full overflow-y-auto scrollbar-thin">
      <div className="mx-auto flex max-w-5xl flex-col gap-6 p-6">
        <header className="space-y-1">
          <h2 className="text-xl font-semibold tracking-tight">{t("title")}</h2>
          {/* max-w on prose: full-width body text at this size is genuinely
              harder to read, and worse in Arabic than in English. */}
          <p className="max-w-2xl text-sm text-muted-foreground">{t("subtitle")}</p>
        </header>

        <Dropzone role="knowledge_base" />

        <section className="flex flex-col gap-3">
          <div className="flex items-baseline justify-between">
            <h3 className="text-sm font-semibold">{t("documents")}</h3>
            <span className="text-xs tabular-nums text-muted-foreground">
              {SAMPLE_DOCUMENTS.length}
            </span>
          </div>
          <DocumentTable documents={SAMPLE_DOCUMENTS} />
        </section>
      </div>
    </div>
  );
}
