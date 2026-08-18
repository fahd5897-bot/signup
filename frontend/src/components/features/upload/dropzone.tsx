"use client";

import { Upload } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { UploadItemRow } from "@/components/features/upload/upload-item-row";
import type { DocumentRole } from "@/lib/api/types";
import { useUploadDocument } from "@/lib/hooks/use-upload-document";
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

export function Dropzone({
  role = "knowledge_base",
  workspaceId,
}: {
  role?: DocumentRole;
  workspaceId?: string;
}) {
  const t = useTranslations("knowledgeBase");
  const [isDragging, setIsDragging] = React.useState(false);
  const [rejected, setRejected] = React.useState<string[]>([]);
  const inputRef = React.useRef<HTMLInputElement>(null);

  // Drag events fire for every child element the cursor crosses, so a plain
  // enter/leave pair flickers constantly once the list below has rows in it.
  const dragDepth = React.useRef(0);

  const { items, upload, retry, dismiss, settle } = useUploadDocument({ role, workspaceId });

  const handleFiles = React.useCallback(
    (fileList: FileList | null) => {
      if (!fileList) return;
      const files = Array.from(fileList);

      // Validate before a socket is opened. Rejecting server-side still costs
      // the user the full upload of a 200 MB file first.
      const bad: string[] = [];
      const good = files.filter((file) => {
        if (!ACCEPTED_MIME.includes(file.type as (typeof ACCEPTED_MIME)[number])) {
          bad.push(`${file.name} — unsupported file type`);
          return false;
        }
        if (file.size > MAX_BYTES) {
          bad.push(`${file.name} — exceeds 200 MB`);
          return false;
        }
        return true;
      });

      setRejected(bad);
      if (good.length > 0) upload(good);
    },
    [upload],
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

      {rejected.length > 0 && (
        <ul className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
          {rejected.map((message) => (
            <li key={message} dir="auto" className="bidi-isolate">
              {message}
            </li>
          ))}
        </ul>
      )}

      {items.length > 0 && (
        <ul className="flex flex-col gap-2">
          {items.map((item) => (
            <UploadItemRow
              key={item.key}
              item={item}
              onRetry={retry}
              onDismiss={dismiss}
              onSettled={settle}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
