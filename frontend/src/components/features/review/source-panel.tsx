"use client";

import { FileText, Table2 } from "lucide-react";
import { useTranslations } from "next-intl";

import type { Citation } from "@/lib/api/types";

/**
 * The exact text a citation points at.
 *
 * This is the whole verification loop in one pane: a reviewer clicks a chip on
 * the answer and reads the source span here without leaving the page. The
 * quoted text is what the backend stored verbatim — never the normalised form
 * used for retrieval, or the words would not match the document the evaluator
 * will open.
 */
export function SourcePanel({ citation }: { citation: Citation | null }) {
  const t = useTranslations("review");
  const w = useTranslations("workspace");

  if (!citation) {
    return (
      <div className="grid h-full place-items-center p-6 text-center">
        <p className="max-w-56 text-sm text-muted-foreground">{t("selectCitation")}</p>
      </div>
    );
  }

  const Icon = citation.chunk_type === "table" ? Table2 : FileText;

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border bg-card px-4">
        <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        <span dir="auto" className="bidi-isolate truncate text-sm font-medium">
          {citation.document_name}
        </span>
        {citation.page_number !== null && (
          <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
            {w("page", { page: citation.page_number })}
          </span>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin p-4">
        {citation.section_path && (
          <p dir="auto" className="bidi-isolate mb-3 text-xs text-muted-foreground">
            {citation.section_path}
          </p>
        )}
        {/* `dir="auto"` per block: a quoted Arabic clause inside an otherwise
            English corpus (and the reverse) both occur in the same tender. */}
        <blockquote
          dir="auto"
          className="bidi-isolate whitespace-pre-wrap border-s-2 border-primary/50 ps-3 text-sm leading-relaxed"
        >
          {citation.quoted_text}
        </blockquote>
      </div>
    </div>
  );
}
