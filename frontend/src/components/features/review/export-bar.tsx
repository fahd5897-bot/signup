"use client";

import { Download, FileSpreadsheet, FileText, Loader2, ShieldAlert } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { ApiRequestError, api } from "@/lib/api/client";
import type { ExportFormat } from "@/lib/api/types";
import { reviewErrorKey, reviewKeys, useReviewProgress } from "@/lib/hooks/use-review";

/**
 * Export readiness and the download itself.
 *
 * The buttons are disabled until every mandatory requirement is approved, for
 * the same reason the API refuses: there is no override. Disabling without
 * saying why would be worse than not showing them, so the outstanding count is
 * always on screen next to them.
 */
export function ExportBar({ workspaceId }: { workspaceId: string }) {
  const t = useTranslations("review");
  const progress = useReviewProgress(workspaceId);
  const [busy, setBusy] = React.useState<ExportFormat | null>(null);
  const [error, setError] = React.useState<unknown>(null);

  // Only meaningful once the gate is open; before that the preview would
  // return the same 409 the export does, and a red error under a button the
  // reviewer has not pressed is noise.
  const preview = useQuery({
    queryKey: reviewKeys.exportPreview(workspaceId),
    queryFn: () => api.exportPreview(workspaceId),
    enabled: Boolean(progress.data?.ready_to_export),
  });

  const ready = progress.data?.ready_to_export ?? false;
  const approved = progress.data?.approved ?? 0;
  const total = progress.data?.total ?? 0;

  async function download(format: ExportFormat) {
    setBusy(format);
    setError(null);
    try {
      const { blob, filename } = await api.exportWorkspace(workspaceId, format);
      // Object URL rather than a plain link to the endpoint: the request needs
      // credentials and a 409 must surface as a catchable error, not as a JSON
      // error body rendered in a new tab.
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col gap-2 border-b border-border bg-card px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <span className="text-sm font-medium">{t("exportTitle")}</span>
          <span className="text-xs tabular-nums text-muted-foreground">
            {t("approvedOf", { approved, total })}
          </span>
        </div>

        <div className="flex flex-wrap gap-2">
          <Button size="sm" disabled={!ready || busy !== null} onClick={() => void download("docx")}>
            {busy === "docx" ? <Loader2 className="animate-spin" aria-hidden /> : <FileText aria-hidden />}
            {t("exportDocx")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={!ready || busy !== null}
            onClick={() => void download("pdf")}
          >
            {busy === "pdf" ? <Loader2 className="animate-spin" aria-hidden /> : <Download aria-hidden />}
            {t("exportPdf")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={!ready || busy !== null}
            onClick={() => void download("matrix")}
          >
            {busy === "matrix" ? (
              <Loader2 className="animate-spin" aria-hidden />
            ) : (
              <FileSpreadsheet aria-hidden />
            )}
            {t("exportMatrix")}
          </Button>
        </div>
      </div>

      <Progress value={total ? (approved / total) * 100 : 0} className="h-1" />

      {!ready && total > 0 && (
        <p className="text-xs text-muted-foreground">
          {t("exportBlockedBy", { count: total - approved })}
        </p>
      )}
      {total === 0 && <p className="text-xs text-muted-foreground">{t("noRequirementsYet")}</p>}

      {ready && (preview.data?.uncited ?? 0) > 0 && (
        <p className="flex items-center gap-1.5 text-xs text-partial">
          <ShieldAlert className="size-3.5 shrink-0" aria-hidden />
          {t("uncitedWarning", { count: preview.data?.uncited ?? 0 })}
        </p>
      )}

      {error !== null && (
        <p role="alert" dir="auto" className="bidi-isolate text-xs text-unverified">
          {t(reviewErrorKey(error))}
          {error instanceof ApiRequestError && error.detail ? ` — ${error.detail}` : ""}
        </p>
      )}
    </div>
  );
}
