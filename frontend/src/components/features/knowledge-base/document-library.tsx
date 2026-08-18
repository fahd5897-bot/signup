"use client";

import { useTranslations } from "next-intl";

import { DocumentTable } from "@/components/features/knowledge-base/document-table";
import { Skeleton } from "@/components/ui/skeleton";
import type { DocumentRole } from "@/lib/api/types";
import { useDocuments } from "@/lib/hooks/use-documents";
import { reviewErrorKey } from "@/lib/hooks/use-review";

/**
 * The tenant's corpus, live.
 *
 * Polls only while something is still being ingested — a 400-page Arabic OCR
 * job otherwise looks stuck until the reviewer reloads, and a fixed interval
 * would keep the browser hitting the API all day for a table that cannot
 * change.
 */
export function DocumentLibrary({
  role,
  workspaceId,
}: {
  role?: DocumentRole;
  workspaceId?: string;
}) {
  const t = useTranslations("knowledgeBase");
  const tError = useTranslations("tenders");
  const query = useDocuments({ role, workspaceId });

  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-semibold">{t("documents")}</h3>
        <span className="text-xs tabular-nums text-muted-foreground">
          {query.data?.total ?? 0}
        </span>
      </div>

      {query.isPending ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : query.isError ? (
        <p role="alert" className="text-sm text-unverified">
          {tError(reviewErrorKey(query.error))}
        </p>
      ) : (
        <DocumentTable documents={query.data?.items ?? []} />
      )}
    </section>
  );
}
