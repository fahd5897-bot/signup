"use client";

import { FileText, Table2 } from "lucide-react";
import { useTranslations } from "next-intl";

import type { Citation } from "@/lib/api/types";
import { cn } from "@/lib/utils/cn";

/**
 * A clickable source reference.
 *
 * This is the component the human-in-the-loop guarantee actually rests on. If
 * a reviewer cannot get from a claim to the exact page that supports it in one
 * click, verification degrades into rubber-stamping and every upstream
 * grounding control stops mattering.
 */
export function CitationChip({
  citation,
  index,
  active,
  onSelect,
}: {
  citation: Citation;
  index: number;
  active?: boolean;
  onSelect?: (citation: Citation) => void;
}) {
  const t = useTranslations("workspace");
  const Icon = citation.chunk_type === "table" ? Table2 : FileText;

  return (
    <button
      type="button"
      onClick={() => onSelect?.(citation)}
      aria-pressed={active}
      title={citation.quoted_text}
      className={cn(
        "group inline-flex max-w-full items-center gap-1.5 rounded-md border px-2 py-1 text-start text-xs transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active
          ? "border-primary bg-primary/10 text-primary"
          : "border-border bg-card text-muted-foreground hover:border-primary/40 hover:text-foreground",
      )}
    >
      <span
        className={cn(
          "grid size-4 shrink-0 place-items-center rounded-sm text-[10px] font-semibold tabular-nums",
          active ? "bg-primary text-primary-foreground" : "bg-secondary text-secondary-foreground",
        )}
      >
        {index + 1}
      </span>
      <Icon className="size-3 shrink-0" aria-hidden />
      <span dir="auto" className="bidi-isolate truncate font-medium">
        {citation.document_name}
      </span>
      {citation.page_number !== null && (
        <span className="shrink-0 tabular-nums opacity-70">
          {t("page", { page: citation.page_number })}
        </span>
      )}
    </button>
  );
}
