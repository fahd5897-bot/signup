"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

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


/**
 * Remove a document and everything indexed from it.
 *
 * Not undoable from the interface, and not cosmetic: the chunks are purged
 * from the vector store, so answers already citing this source keep a citation
 * that no longer resolves. That is the correct trade — a customer removing a
 * file has usually decided it must not be quoted again — but it is why the
 * control asks first.
 */
export function useDeleteDocument() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (documentId: string) => api.deleteDocument(documentId),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}
