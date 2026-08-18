"use client";

import { AlertTriangle, FileSpreadsheet, FileText, FileType, Loader2, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";

import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { DocumentSummary } from "@/lib/api/types";
import { useDeleteDocument } from "@/lib/hooks/use-documents";
import { cn } from "@/lib/utils/cn";

const TERMINAL_OK = new Set(["ready"]);
const TERMINAL_BAD = new Set(["failed", "quarantined"]);

function iconFor(mime: string) {
  if (mime.includes("spreadsheet") || mime.includes("excel") || mime === "text/csv") {
    return FileSpreadsheet;
  }
  if (mime === "application/pdf") return FileType;
  return FileText;
}

export function DocumentTable({ documents }: { documents: DocumentSummary[] }) {
  const t = useTranslations("knowledgeBase");
  const tStatus = useTranslations("status");
  const tQuality = useTranslations("quality");

  if (documents.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border px-6 py-12 text-center">
        <p className="text-sm font-medium">{t("empty")}</p>
        <p className="mt-1 text-sm text-muted-foreground">{t("emptyHint")}</p>
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-muted/40 text-xs text-muted-foreground">
            {/* text-start, not text-left — the header must follow direction. */}
            <th className="px-4 py-2.5 text-start font-medium">{t("columns.name")}</th>
            <th className="px-4 py-2.5 text-start font-medium">{t("columns.status")}</th>
            <th className="px-4 py-2.5 text-start font-medium">{t("columns.language")}</th>
            <th className="px-4 py-2.5 text-end font-medium">{t("columns.pages")}</th>
            <th className="px-4 py-2.5 text-end font-medium">{t("columns.chunks")}</th>
            <th className="px-4 py-2.5 text-start font-medium">{t("columns.quality")}</th>
            {/* No header text: the column holds one icon control, and a label
                would read as a data column in a screen reader's table summary. */}
            <th className="px-4 py-2.5">
              <span className="sr-only">{t("columns.actions")}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {documents.map((doc) => {
            const Icon = iconFor(doc.mime_type);
            const inFlight = !TERMINAL_OK.has(doc.status) && !TERMINAL_BAD.has(doc.status);

            return (
              <tr key={doc.id} className="border-b border-border last:border-0">
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2.5">
                    <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                    <span dir="auto" className="bidi-isolate truncate font-medium">
                      {doc.filename}
                    </span>
                  </div>
                  {doc.failure_reason && (
                    // Server-generated text whose language does not follow the
                    // interface locale: an English parser message inside an
                    // Arabic layout lands its full stop on the wrong end
                    // without this, which reads as a rendering fault in the
                    // one place the reader is already worried.
                    <p dir="auto" className="bidi-isolate mt-1 text-xs text-destructive">
                      {doc.failure_reason}
                    </p>
                  )}
                </td>

                <td className="px-4 py-3">
                  <Badge
                    variant={
                      TERMINAL_OK.has(doc.status)
                        ? "verified"
                        : TERMINAL_BAD.has(doc.status)
                          ? "unverified"
                          : "secondary"
                    }
                  >
                    {inFlight && (
                      <span className="size-1.5 animate-pulse rounded-full bg-current" />
                    )}
                    {tStatus(doc.status)}
                  </Badge>
                </td>

                <td className="px-4 py-3 uppercase text-muted-foreground">{doc.language}</td>
                <td className="px-4 py-3 text-end tabular-nums text-muted-foreground">
                  {doc.page_count ?? "—"}
                </td>
                <td className="px-4 py-3 text-end tabular-nums text-muted-foreground">
                  {doc.chunk_count || "—"}
                </td>

                <td className="px-4 py-3">
                  {/*
                    The Arabic-scan failure mode: a document parses without
                    error yet yields text on a third of its pages. Surfacing it
                    here is what stops a near-empty document being trusted as
                    evidence — it is invisible in every other column.
                  */}
                  {doc.extraction_quality === "good" ? (
                    <span className="text-muted-foreground">{tQuality("good")}</span>
                  ) : doc.extraction_quality === "unknown" ? (
                    <span className="text-muted-foreground">—</span>
                  ) : (
                    <span
                      title={
                        doc.extraction_quality === "poor"
                          ? tQuality("poorHint")
                          : tQuality("degradedHint")
                      }
                      className={cn(
                        "inline-flex items-center gap-1.5",
                        doc.extraction_quality === "poor" ? "text-unverified" : "text-partial",
                      )}
                    >
                      <AlertTriangle className="size-3.5" aria-hidden />
                      {tQuality(doc.extraction_quality)}
                      {doc.text_extraction_ratio !== null && (
                        <span className="tabular-nums">
                          ({Math.round(doc.text_extraction_ratio * 100)}%)
                        </span>
                      )}
                    </span>
                  )}
                </td>

                <td className="px-4 py-3 text-end">
                  <DeleteDocumentButton document={doc} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}


/**
 * Two-step removal, in place.
 *
 * A confirmation rather than a single click, because this purges the
 * document's chunks from the vector store: answers already citing it keep a
 * citation that no longer resolves. Two steps in the row instead of a modal —
 * a dialog for one destructive action on one row is more ceremony than the
 * decision needs, and modals in a right-to-left layout are their own bug farm.
 */
function DeleteDocumentButton({ document }: { document: DocumentSummary }) {
  const t = useTranslations("knowledgeBase");
  const remove = useDeleteDocument();
  const [confirming, setConfirming] = React.useState(false);

  // Drop back out of the armed state if the reviewer moves on without
  // deciding, so a stray click minutes later cannot delete anything.
  React.useEffect(() => {
    if (!confirming) return;
    const timer = setTimeout(() => setConfirming(false), 5000);
    return () => clearTimeout(timer);
  }, [confirming]);

  if (remove.isPending) {
    return <Loader2 className="ms-auto size-4 animate-spin text-muted-foreground" aria-hidden />;
  }

  if (confirming) {
    return (
      <Button
        size="sm"
        variant="outline"
        className="text-unverified"
        onClick={() => remove.mutate(document.id)}
      >
        {t("confirmDelete")}
      </Button>
    );
  }

  return (
    <Button
      size="sm"
      variant="ghost"
      onClick={() => setConfirming(true)}
      aria-label={t("deleteDocument", { name: document.filename })}
      title={t("delete")}
    >
      <Trash2 aria-hidden />
    </Button>
  );
}
