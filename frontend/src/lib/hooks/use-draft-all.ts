"use client";

import { useQueryClient } from "@tanstack/react-query";
import * as React from "react";

import { api } from "@/lib/api/client";
import type { ProposalSummary } from "@/lib/api/types";
import { reviewKeys } from "@/lib/hooks/use-review";

/**
 * Draft answers for every requirement that has none.
 *
 * Sequential takes minutes on a forty-item matrix; unbounded trips the API rate
 * limit and turns a slow fill into a failed one. Four sits comfortably inside a
 * standard tier while keeping the progress visibly moving.
 */
const CONCURRENCY = 4;

export function useDraftAll(workspaceId: string) {
  const client = useQueryClient();
  const [running, setRunning] = React.useState(false);
  const [done, setDone] = React.useState(0);
  const [failed, setFailed] = React.useState(0);
  const cancelRef = React.useRef(false);

  const start = React.useCallback(
    async (items: ProposalSummary[]) => {
      // Only what has no answer yet. Re-running after fixing a source document
      // must never discard work a reviewer has already checked — and an
      // approved answer is exactly what a blind re-draft would supersede.
      const queue = items.filter(
        (item) => item.status === "draft" || item.status === "abstained",
      );
      if (queue.length === 0) return;

      cancelRef.current = false;
      setRunning(true);
      setDone(0);
      setFailed(0);

      const pending = [...queue];
      const workers = Array.from({ length: Math.min(CONCURRENCY, pending.length) }, async () => {
        for (let next = pending.shift(); next && !cancelRef.current; next = pending.shift()) {
          try {
            const proposal = await api.proposal(next.id);
            await api.generateAnswer(
              {
                requirement_ref: proposal.requirement_ref,
                requirement_text: proposal.requirement_text,
                section_path: proposal.section_path,
                is_mandatory: proposal.is_mandatory,
              },
              workspaceId,
            );
            setDone((n) => n + 1);
          } catch {
            // One requirement failing must not abandon the other thirty-nine.
            // The failure is visible in the count and the row keeps its
            // unanswered status, so nothing is silently marked complete.
            setFailed((n) => n + 1);
          }
        }
      });

      await Promise.all(workers);
      setRunning(false);
      void client.invalidateQueries({ queryKey: reviewKeys.queue(workspaceId) });
      void client.invalidateQueries({ queryKey: reviewKeys.progress(workspaceId) });
    },
    [client, workspaceId],
  );

  const cancel = React.useCallback(() => {
    cancelRef.current = true;
  }, []);

  return { start, cancel, running, done, failed };
}
