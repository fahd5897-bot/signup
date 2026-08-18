"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import * as React from "react";

import { ApiRequestError, api } from "@/lib/api/client";
import type { DocumentRole } from "@/lib/api/types";

export type UploadPhase = "uploading" | "processing" | "ready" | "failed" | "error";

export interface UploadItem {
  /** Stable client-side key. The server document ID only exists after the 202. */
  key: string;
  file: File;
  phase: UploadPhase;
  /** 0–100, meaningful only while `phase === "uploading"`. */
  progress: number;
  documentId?: string;
  /** Backend error slug, so the UI can branch without matching on text. */
  errorSlug?: string;
  errorMessage?: string;
}

let counter = 0;
const nextKey = () => `upload-${++counter}`;

/**
 * Manages a batch of concurrent uploads.
 *
 * Progress lives in component state rather than in React Query, deliberately.
 * A mutation models one request's lifecycle; upload progress is a stream of
 * intermediate values with no equivalent in that model, and forcing it through
 * the cache would mean a re-render storm on every XHR progress event across
 * every file. React Query still owns the request lifecycle, retry policy, and
 * the cache invalidation that follows a successful upload.
 */
export function useUploadDocument({
  role = "knowledge_base",
  workspaceId,
}: {
  role?: DocumentRole;
  workspaceId?: string;
} = {}) {
  const queryClient = useQueryClient();
  const [items, setItems] = React.useState<UploadItem[]>([]);
  const controllers = React.useRef(new Map<string, AbortController>());

  const patch = React.useCallback((key: string, next: Partial<UploadItem>) => {
    setItems((prev) => prev.map((i) => (i.key === key ? { ...i, ...next } : i)));
  }, []);

  const mutation = useMutation({
    mutationFn: async ({ key, file }: { key: string; file: File }) => {
      const controller = new AbortController();
      controllers.current.set(key, controller);
      try {
        return await api.uploadDocument(file, {
          role,
          workspaceId,
          signal: controller.signal,
          onProgress: (progress) => patch(key, { progress }),
        });
      } finally {
        controllers.current.delete(key);
      }
    },
    onSuccess: (accepted, { key }) => {
      // 202, not 200: the bytes are stored and parsing is queued. The document
      // is not citable yet, so this is a distinct phase rather than completion.
      patch(key, {
        phase: "processing",
        progress: 100,
        documentId: accepted.resource_id,
      });
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (error, { key }) => {
      patch(key, {
        phase: "error",
        errorSlug: error instanceof ApiRequestError ? error.slug : "unknown_error",
        errorMessage:
          error instanceof ApiRequestError
            ? (error.detail ?? error.message)
            : "Upload failed",
      });
    },
  });

  const upload = React.useCallback(
    (files: File[] | FileList) => {
      for (const file of Array.from(files)) {
        const key = nextKey();
        setItems((prev) => [...prev, { key, file, phase: "uploading", progress: 0 }]);
        mutation.mutate({ key, file });
      }
    },
    [mutation],
  );

  const retry = React.useCallback(
    (key: string) => {
      const item = items.find((i) => i.key === key);
      if (!item) return;
      patch(key, { phase: "uploading", progress: 0, errorSlug: undefined, errorMessage: undefined });
      mutation.mutate({ key, file: item.file });
    },
    [items, mutation, patch],
  );

  const cancel = React.useCallback((key: string) => {
    controllers.current.get(key)?.abort();
    setItems((prev) => prev.filter((i) => i.key !== key));
  }, []);

  const dismiss = React.useCallback((key: string) => {
    setItems((prev) => prev.filter((i) => i.key !== key));
  }, []);

  /** Called by the status poller when ingestion reaches a terminal state. */
  const settle = React.useCallback(
    (documentId: string, phase: "ready" | "failed", errorMessage?: string) => {
      setItems((prev) =>
        prev.map((i) => (i.documentId === documentId ? { ...i, phase, errorMessage } : i)),
      );
    },
    [],
  );

  return { items, upload, retry, cancel, dismiss, settle };
}
