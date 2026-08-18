"use client";

import { useQuery } from "@tanstack/react-query";
import * as React from "react";

import { api } from "@/lib/api/client";

/** Backoff schedule, in ms, indexed by poll count. */
const INTERVALS = [1_000, 1_000, 2_000, 2_000, 3_000, 5_000, 8_000];
const MAX_INTERVAL = 15_000;

/**
 * Polls one document until ingestion reaches a terminal state.
 *
 * Polling rather than websockets: ingestion is minutes-long and low-frequency,
 * a document page has at most a handful in flight, and a socket per upload
 * costs a persistent connection to save a request every few seconds.
 *
 * The interval widens as it goes. A 200-page scanned Arabic tender can take
 * several minutes of OCR, and a fixed one-second poll would issue a few hundred
 * pointless requests before it finished.
 */
export function useDocumentStatus(
  documentId: string | undefined,
  options: { enabled?: boolean; onSettled?: (status: string, reason?: string) => void } = {},
) {
  const { enabled = true, onSettled } = options;
  const notified = React.useRef(false);

  const query = useQuery({
    queryKey: ["document-status", documentId],
    queryFn: () => api.getDocumentStatus(documentId!),
    enabled: Boolean(documentId) && enabled,
    refetchInterval: (query) => {
      // `false` stops the poller. Returning a number unconditionally is how
      // these quietly keep running until the tab closes.
      if (query.state.data?.is_terminal) return false;

      // Indexed by the query's own fetch count, NOT by a ref incremented in
      // this callback: React Query evaluates refetchInterval on every render,
      // not once per fetch, so a ref races ahead of the actual poll count and
      // the backoff jumps to its maximum within a second or two.
      const fetches = query.state.dataUpdateCount + query.state.fetchFailureCount;
      return INTERVALS[fetches] ?? MAX_INTERVAL;
    },
    // Ingestion status is inherently live; a cached value is never useful here.
    staleTime: 0,
  });

  React.useEffect(() => {
    if (!query.data?.is_terminal || notified.current) return;
    notified.current = true;
    onSettled?.(query.data.status, query.data.failure_reason ?? undefined);
  }, [query.data, onSettled]);

  return query;
}
