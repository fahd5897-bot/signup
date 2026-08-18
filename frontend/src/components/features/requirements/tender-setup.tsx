"use client";

import { AlertTriangle, FileSearch, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Dropzone } from "@/components/features/upload/dropzone";
import { Button } from "@/components/ui/button";
import { useDocuments } from "@/lib/hooks/use-documents";
import { reviewErrorKey, useExtractRequirements } from "@/lib/hooks/use-review";

/**
 * Getting from an empty workspace to a compliance matrix.
 *
 * Shown only while the matrix is empty. Once requirements exist this step is
 * done, and leaving a prominent "extract requirements" button on screen invites
 * a re-run that a reviewer would reasonably expect to be idempotent — it is,
 * but only because the service skips references that already have an answer.
 */
export function TenderSetup({ workspaceId }: { workspaceId: string }) {
  const t = useTranslations("review");
  const tError = useTranslations("tenders");
  const documents = useDocuments({ role: "tender", workspaceId });
  const extract = useExtractRequirements(workspaceId);
  const [selected, setSelected] = React.useState<string | null>(null);

  const ready = (documents.data?.items ?? []).filter((doc) => doc.status === "ready");
  const pending = (documents.data?.items ?? []).filter(
    (doc) => !["ready", "failed", "quarantined"].includes(doc.status),
  );
  const chosen = selected ?? ready[0]?.id ?? null;

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-4 p-6">
      <header className="space-y-1">
        <h3 className="text-sm font-semibold">{t("setupTitle")}</h3>
        <p className="text-sm text-muted-foreground">{t("setupHint")}</p>
      </header>

      {/* Role `tender`, not `knowledge_base`: a tender document is the
          question. Indexed as knowledge it would answer requirements with the
          customer's own wording quoted back at them. */}
      <Dropzone role="tender" workspaceId={workspaceId} />

      {pending.length > 0 && (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="size-3 animate-spin" aria-hidden />
          {t("stillIngesting", { count: pending.length })}
        </p>
      )}

      {ready.length > 0 && (
        <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
          <label className="flex flex-col gap-1.5 text-xs font-medium">
            {t("chooseTender")}
            <select
              value={chosen ?? ""}
              onChange={(e) => setSelected(e.target.value)}
              dir="auto"
              className="h-9 rounded-md border border-border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {ready.map((doc) => (
                <option key={doc.id} value={doc.id}>
                  {doc.filename}
                </option>
              ))}
            </select>
          </label>

          <Button
            size="sm"
            disabled={!chosen || extract.isPending}
            onClick={() => chosen && extract.mutate({ documentId: chosen })}
          >
            {extract.isPending ? (
              <Loader2 className="animate-spin" aria-hidden />
            ) : (
              <FileSearch aria-hidden />
            )}
            {extract.isPending ? t("extracting") : t("extract")}
          </Button>

          {extract.isPending && (
            // Several model calls over a long document; saying so beats a
            // spinner that looks hung on a 200-page tender.
            <p className="text-xs text-muted-foreground">{t("extractingHint")}</p>
          )}

          {extract.isError && (
            <p role="alert" className="text-xs text-unverified">
              {tError(reviewErrorKey(extract.error))}
            </p>
          )}

          {extract.isSuccess && extract.data.dropped > 0 && (
            // Surfaced, not hidden: a document producing many drops is either
            // badly scanned or being summarised rather than quoted, and the
            // matrix should not be trusted until someone looks.
            <p className="flex items-start gap-1.5 text-xs text-partial">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              {t("extractionDropped", { count: extract.data.dropped })}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
