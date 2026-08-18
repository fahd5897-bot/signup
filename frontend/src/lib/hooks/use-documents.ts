"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import type { DocumentRole } from "@/lib/api/types";

export const documentKeys = {
  list: (role?: DocumentRole, workspaceId?: string) =>
    ["documents", role ?? "all", workspaceId ?? "all"] as const,
};

/**
 * The tenant's corpus.
 *
 * Polls while anything is still being ingested and stops once every row is
 * terminal. A fixed interval would keep a browser hitting the API all day for
 * a table that cannot change; no polling at all would leave a 400-page Arabic
 * OCR job looking stuck until the reviewer reloads.
 */
export function useDocuments(params: { role?: DocumentRole; workspaceId?: string } = {}) {
  return useQuery({
    queryKey: documentKeys.list(params.role, params.workspaceId),
    queryFn: () => api.listDocuments(params),
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? [];
      const busy = items.some(
        (doc) => !["ready", "failed", "quarantined"].includes(doc.status),
      );
      return busy ? 4000 : false;
    },
  });
}
