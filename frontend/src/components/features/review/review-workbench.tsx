"use client";

import { Loader2, Sparkles, X } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { ExportBar } from "@/components/features/review/export-bar";
import { ProposalDetail } from "@/components/features/review/proposal-detail";
import { ReviewQueue } from "@/components/features/review/review-queue";
import { Button } from "@/components/ui/button";
import type { Citation } from "@/lib/api/types";
import { useDraftAll } from "@/lib/hooks/use-draft-all";
import { useReviewQueue } from "@/lib/hooks/use-review";
import { useSession } from "@/lib/hooks/use-session";

/**
 * The review workbench: queue on the left, one answer on the right.
 *
 * `canApprove` is derived from the session role only to keep the controls
 * honest — the server decides. An SME who is shown an approve button and then
 * refused learns to distrust the interface; one who never sees it learns the
 * workflow.
 */
export function ReviewWorkbench({
  workspaceId,
  activeCitation,
  onSelectCitation,
}: {
  workspaceId: string;
  activeCitation: Citation | null;
  onSelectCitation: (citation: Citation | null) => void;
}) {
  const t = useTranslations("review");
  const { user } = useSession();
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const queue = useReviewQueue(workspaceId);
  const draftAll = useDraftAll(workspaceId);

  const canApprove = user?.role === "owner" || user?.role === "bid_manager";
  const unanswered = (queue.data?.items ?? []).filter(
    (item) => item.status === "draft" || item.status === "abstained",
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ExportBar workspaceId={workspaceId} />

      <div className="flex min-h-0 flex-1">
        <div className="flex w-64 shrink-0 flex-col border-e border-border">
          <div className="flex h-11 shrink-0 items-center justify-between gap-2 border-b border-border px-3">
            <span className="truncate text-xs font-medium text-muted-foreground">
              {t("queueTitle")}
            </span>
            {draftAll.running ? (
              <Button size="sm" variant="ghost" onClick={draftAll.cancel}>
                <X aria-hidden />
                {t("draftAllRunning", { done: draftAll.done, total: unanswered.length })}
              </Button>
            ) : (
              unanswered.length > 0 && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => void draftAll.start(unanswered)}
                >
                  <Sparkles aria-hidden />
                  {t("draftAll", { count: unanswered.length })}
                </Button>
              )
            )}
          </div>
          {draftAll.running && (
            <p className="flex items-center gap-1.5 border-b border-border px-3 py-1.5 text-[11px] text-muted-foreground">
              <Loader2 className="size-3 animate-spin" aria-hidden />
              {t("draftAllProgress", { done: draftAll.done, failed: draftAll.failed })}
            </p>
          )}
          <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin">
          <ReviewQueue
            workspaceId={workspaceId}
            selectedId={selectedId}
            onSelect={(item) => setSelectedId(item.id)}
          />
          </div>
        </div>

        <div className="min-w-0 flex-1 overflow-y-auto scrollbar-thin">
          {selectedId ? (
            <ProposalDetail
              proposalId={selectedId}
              workspaceId={workspaceId}
              canApprove={canApprove}
              activeCitation={activeCitation}
              onSelectCitation={onSelectCitation}
            />
          ) : (
            <EmptyState />
          )}
        </div>
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="grid h-full place-items-center p-8 text-center text-sm text-muted-foreground">
      <p>{/* Intentionally quiet: the queue beside it is the instruction. */}</p>
    </div>
  );
}
