"use client";

import { AlertCircle, CheckCircle2, FileText, Upload, X } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { ApiRequestError, api } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils/cn";

/** Mirrors the backend allowlist in `app/schemas/document.py`. */
const ACCEPTED_MIME = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/msword",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.ms-excel",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "text/plain",
  "text/csv",
  "text/html",
] as const;

const MAX_BYTES = 200 * 1024 * 1024;

type UploadState = "uploading" | "processing" | "ready" | "error";

export interface UploadItem {
  /** Client-side key; the server document ID arrives with the 202. */
  key: string;
  file: File;
  state: UploadState;
  progress: number;
  documentId?: string;
  error?: string;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

export function Dropzone({
  role = "knowledge_base",
  workspaceId,
  onUploaded,
}: {
  role?: string;
  workspaceId?: string;
  onUploaded?: (documentId: string) => void;
}) {
  const t = useTranslations("knowledgeBase");
  const [isDragging, setIsDragging] = React.useState(false);
  const [items, setItems] = React.useState<UploadItem[]>([]);
  const inputRef = React.useRef<HTMLInputElement>(null);

  // Drag events fire for every child element the cursor crosses, so a plain
  // enter/leave pair flickers constantly. Counting depth is what makes the
  // highlight stable over a zone that contains a list.
  const dragDepth = React.useRef(0);

  const update = React.useCallback((key: string, patch: Partial<UploadItem>) => {
    setItems((prev) => prev.map((i) => (i.key === key ? { ...i, ...patch } : i)));
  }, []);

  const startUpload = React.useCallback(
    async (file: File) => {
      const key = `${file.name}:${file.size}:${Date.now()}`;

      // Validate before opening a socket. A 200 MB file rejected server-side
      // still costs the user the full upload first.
      if (!ACCEPTED_MIME.includes(file.type as (typeof ACCEPTED_MIME)[number])) {
        setItems((prev) => [
          ...prev,
          { key, file, state: "error", progress: 0, error: "Unsupported file type" },
        ]);
        return;
      }
      if (file.size > MAX_BYTES) {
        setItems((prev) => [
          ...prev,
          { key, file, state: "error", progress: 0, error: "File exceeds 200 MB" },
        ]);
        return;
      }

      setItems((prev) => [...prev, { key, file, state: "uploading", progress: 0 }]);

      try {
        const accepted = await api.uploadDocument(file, {
          role,
          workspaceId,
          onProgress: (percent) => update(key, { progress: percent }),
        });
        // 202: bytes are stored, parsing is queued. The bar is full but the
        // document is not usable yet, so the state changes rather than
        // disappearing — "uploaded" and "ready to cite" are different things.
        update(key, {
          state: "processing",
          progress: 100,
          documentId: accepted.resource_id,
        });
        onUploaded?.(accepted.resource_id);
      } catch (error) {
        const message =
          error instanceof ApiRequestError
            ? (error.detail ?? error.message)
            : "Upload failed";
        update(key, { state: "error", error: message });
      }
    },
    [role, workspaceId, onUploaded, update],
  );

  const handleFiles = React.useCallback(
    (fileList: FileList | null) => {
      if (!fileList) return;
      Array.from(fileList).forEach((file) => void startUpload(file));
    },
    [startUpload],
  );

  return (
    <div className="flex flex-col gap-3">
      <div
        onDragEnter={(e) => {
          e.preventDefault();
          dragDepth.current += 1;
          setIsDragging(true);
        }}
        onDragLeave={(e) => {
          e.preventDefault();
          dragDepth.current -= 1;
          if (dragDepth.current === 0) setIsDragging(false);
        }}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          dragDepth.current = 0;
          setIsDragging(false);
          handleFiles(e.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            inputRef.current?.click();
          }
        }}
        role="button"
        tabIndex={0}
        aria-label={t("dropTitle")}
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-6 py-12 text-center transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          isDragging
            ? "border-primary bg-primary/5"
            : "border-border bg-card hover:border-muted-foreground/40 hover:bg-accent/40",
        )}
      >
        <div
          className={cn(
            "grid size-11 place-items-center rounded-full transition-colors",
            isDragging ? "bg-primary/15 text-primary" : "bg-secondary text-muted-foreground",
          )}
        >
          <Upload className="size-5" aria-hidden />
        </div>
        <div className="space-y-0.5">
          <p className="text-sm font-medium">{t("dropTitle")}</p>
          <p className="text-sm text-muted-foreground">{t("dropSubtitle")}</p>
        </div>
        <p className="text-xs text-muted-foreground">{t("dropFormats")}</p>

        <input
          ref={inputRef}
          type="file"
          multiple
          className="sr-only"
          accept={ACCEPTED_MIME.join(",")}
          onChange={(e) => {
            handleFiles(e.target.files);
            // Reset so re-selecting the same file fires change again.
            e.target.value = "";
          }}
        />
      </div>

      {items.length > 0 && (
        <ul className="flex flex-col gap-2">
          {items.map((item) => (
            <li
              key={item.key}
              className="flex items-center gap-3 rounded-md border border-border bg-card p-3"
            >
              <FileText className="size-4 shrink-0 text-muted-foreground" aria-hidden />

              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-3">
                  {/* bidi-isolate: an Arabic filename next to a Latin size
                      would otherwise reorder the whole row. */}
                  <span dir="auto" className="bidi-isolate truncate text-sm font-medium">
                    {item.file.name}
                  </span>
                  <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                    {formatBytes(item.file.size)}
                  </span>
                </div>

                {item.state === "uploading" && (
                  <div className="mt-1.5 flex items-center gap-2">
                    <Progress value={item.progress} className="flex-1" />
                    <span className="w-9 shrink-0 text-end text-xs tabular-nums text-muted-foreground">
                      {item.progress}%
                    </span>
                  </div>
                )}
                {item.state === "processing" && (
                  <p className="mt-1 text-xs text-muted-foreground">{t("processing")}…</p>
                )}
                {item.state === "error" && (
                  <p className="mt-1 text-xs text-destructive">{item.error}</p>
                )}
              </div>

              {item.state === "processing" && (
                <div className="size-4 shrink-0 animate-spin rounded-full border-2 border-muted border-t-primary" />
              )}
              {item.state === "ready" && (
                <CheckCircle2 className="size-4 shrink-0 text-verified" aria-hidden />
              )}
              {item.state === "error" && (
                <>
                  <AlertCircle className="size-4 shrink-0 text-destructive" aria-hidden />
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-7 shrink-0"
                    onClick={() =>
                      setItems((prev) => prev.filter((i) => i.key !== item.key))
                    }
                    aria-label="Dismiss"
                  >
                    <X className="size-3.5" aria-hidden />
                  </Button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
