"use client";

import { AlertCircle, CheckCircle2, FileText, RotateCcw, X } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import type { UploadItem } from "@/lib/hooks/use-upload-document";
import { useDocumentStatus } from "@/lib/hooks/use-document-status";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

/**
 * One row of the upload list.
 *
 * Its own component so each in-flight document gets its own polling hook —
 * hooks cannot be called in a loop, and a single shared poller would have to
 * multiplex several documents by hand.
 */
export function UploadItemRow({
  item,
  onRetry,
  onDismiss,
  onSettled,
}: {
  item: UploadItem;
  onRetry: (key: string) => void;
  onDismiss: (key: string) => void;
  onSettled: (documentId: string, phase: "ready" | "failed", reason?: string) => void;
}) {
  const t = useTranslations("knowledgeBase");
  const tStatus = useTranslations("status");

  const { data } = useDocumentStatus(item.documentId, {
    enabled: item.phase === "processing",
    onSettled: (status, reason) =>
      onSettled(item.documentId!, status === "ready" ? "ready" : "failed", reason),
  });

  return (
    <li className="flex items-center gap-3 rounded-md border border-border bg-card p-3">
      <FileText className="size-4 shrink-0 text-muted-foreground" aria-hidden />

      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-3">
          <span dir="auto" className="bidi-isolate truncate text-sm font-medium">
            {item.file.name}
          </span>
          <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
            {formatBytes(item.file.size)}
          </span>
        </div>

        {item.phase === "uploading" && (
          <div className="mt-1.5 flex items-center gap-2">
            <Progress value={item.progress} className="flex-1" />
            <span className="w-9 shrink-0 text-end text-xs tabular-nums text-muted-foreground">
              {item.progress}%
            </span>
          </div>
        )}

        {item.phase === "processing" && (
          <p className="mt-1 text-xs text-muted-foreground">
            {/* The live ingestion stage, not a generic spinner: OCR on a
                200-page scan takes minutes, and "Parsing" vs "Indexing" is the
                difference between "working" and "stuck". */}
            {data ? tStatus(data.status) : t("processing")}…
          </p>
        )}

        {item.phase === "ready" && (
          <p className="mt-1 text-xs text-verified">
            {tStatus("ready")}
            {data?.chunk_count ? ` · ${data.chunk_count}` : ""}
          </p>
        )}

        {(item.phase === "error" || item.phase === "failed") && (
          <p className="mt-1 text-xs text-destructive">{item.errorMessage}</p>
        )}
      </div>

      {item.phase === "processing" && (
        <div className="size-4 shrink-0 animate-spin rounded-full border-2 border-muted border-t-primary" />
      )}
      {item.phase === "ready" && (
        <CheckCircle2 className="size-4 shrink-0 text-verified" aria-hidden />
      )}

      {(item.phase === "error" || item.phase === "failed") && (
        <>
          <AlertCircle className="size-4 shrink-0 text-destructive" aria-hidden />
          {/* Retry only for transport failures. A 415 or a checksum mismatch
              will fail identically on every attempt, so offering the button
              would just invite the user to re-send a 200 MB file for nothing. */}
          {item.phase === "error" && item.errorSlug === "network_error" && (
            <Button
              variant="ghost"
              size="icon"
              className="size-7 shrink-0"
              onClick={() => onRetry(item.key)}
              aria-label="Retry"
            >
              <RotateCcw className="size-3.5" aria-hidden />
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon"
            className="size-7 shrink-0"
            onClick={() => onDismiss(item.key)}
            aria-label="Dismiss"
          >
            <X className="size-3.5" aria-hidden />
          </Button>
        </>
      )}
    </li>
  );
}
