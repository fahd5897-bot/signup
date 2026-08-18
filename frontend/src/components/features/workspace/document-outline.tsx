"use client";

import { ChevronRight, FileText } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import type { Citation } from "@/lib/api/types";
import { cn } from "@/lib/utils/cn";

export interface OutlineNode {
  id: string;
  label: string;
  page: number;
  depth: number;
}

/**
 * Left pane: document outline, plus the cited passage when one is selected.
 *
 * An outline rather than an embedded PDF render in this MVP. `react-pdf` is in
 * the dependency set and the swap is local to this component, but the outline
 * is what actually gets a reviewer to the right clause quickly — and the cited
 * text is shown verbatim beneath it, which is the part that has to be exact.
 */
export function DocumentOutline({
  nodes,
  activeCitation,
  documentTitle,
}: {
  nodes: OutlineNode[];
  activeCitation: Citation | null;
  documentTitle: string;
}) {
  const t = useTranslations("workspace");
  const activePage = activeCitation?.page_number ?? null;

  return (
    <div className="flex h-full flex-col bg-card">
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-4">
        <FileText className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        <span dir="auto" className="bidi-isolate truncate text-sm font-medium">
          {documentTitle}
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin p-2">
        <p className="px-2 pb-2 pt-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {t("outline")}
        </p>
        <ul className="flex flex-col gap-0.5">
          {nodes.map((node) => {
            const onActivePage = activePage !== null && node.page === activePage;
            return (
              <li key={node.id}>
                <button
                  type="button"
                  // Logical padding scales with depth so nesting indents from
                  // the correct edge in both directions.
                  style={{ paddingInlineStart: `${0.5 + node.depth * 0.85}rem` }}
                  className={cn(
                    "flex w-full items-center gap-1.5 rounded-md py-1.5 pe-2 text-start text-sm transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    onActivePage
                      ? "bg-primary/10 font-medium text-primary"
                      : "text-muted-foreground hover:bg-accent hover:text-foreground",
                  )}
                >
                  {node.depth === 0 && (
                    <ChevronRight
                      className="size-3 shrink-0 opacity-50 rtl:rotate-180"
                      aria-hidden
                    />
                  )}
                  <span dir="auto" className="bidi-isolate truncate">{node.label}</span>
                  <span className="ms-auto shrink-0 text-xs tabular-nums opacity-60">
                    {node.page}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>

      {activeCitation && (
        <div className="shrink-0 border-t border-border bg-muted/30 p-4">
          <div className="mb-2 flex items-baseline justify-between gap-2">
            <span dir="auto" className="bidi-isolate truncate text-xs font-medium">
              {activeCitation.document_name}
            </span>
            {activeCitation.page_number !== null && (
              <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                {t("page", { page: activeCitation.page_number })}
              </span>
            )}
          </div>
          {activeCitation.section_path && (
            <p className="mb-2 truncate text-xs text-muted-foreground">
              {activeCitation.section_path}
            </p>
          )}
          {/*
            The cited span verbatim. `quoted_text` is the original source text,
            not the retrieval-normalised form — for Arabic those differ
            (diacritics stripped, alef forms folded), and showing the normalised
            version would not match the document the reviewer is checking.
          */}
          <blockquote
            dir="auto"
            className="bidi-isolate border-s-2 border-primary/40 ps-3 text-sm leading-relaxed"
          >
            {activeCitation.quoted_text}
          </blockquote>
        </div>
      )}
    </div>
  );
}
