"use client";

import { AlertTriangle, FileSpreadsheet, FileText, FileType } from "lucide-react";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import type { DocumentSummary } from "@/lib/api/types";
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
                    <p className="mt-1 text-xs text-destructive">{doc.failure_reason}</p>
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
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
