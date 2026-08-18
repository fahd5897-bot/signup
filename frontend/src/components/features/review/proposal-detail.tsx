"use client";

import { Loader2, Pencil, RotateCcw, Sparkles } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { CitationChip } from "@/components/features/citations/citation-chip";
import { GroundingBadge } from "@/components/features/citations/grounding-badge";
import { ReviewActions } from "@/components/features/review/review-actions";
import { StatusBadge } from "@/components/features/review/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { Citation } from "@/lib/api/types";
import {
  reviewErrorKey,
  useEditProposal,
  useGenerateForRequirement,
  useProposal,
} from "@/lib/hooks/use-review";

/**
 * One answer, its evidence, and the decision.
 *
 * The generated text and the reviewer's edit are shown as two separate things
 * rather than one merged field. That is what makes the human-vs-model delta
 * visible to the person creating it — and it matches the database, where
 * `answer_text` is never overwritten.
 */
export function ProposalDetail({
  proposalId,
  workspaceId,
  canApprove,
  activeCitation,
  onSelectCitation,
}: {
  proposalId: string;
  workspaceId: string;
  canApprove: boolean;
  activeCitation: Citation | null;
  onSelectCitation: (citation: Citation | null) => void;
}) {
  const t = useTranslations("review");
  const query = useProposal(proposalId);
  const edit = useEditProposal(workspaceId);
  const generate = useGenerateForRequirement(workspaceId);

  const [draft, setDraft] = React.useState<string | null>(null);

  // Reset the editor whenever a different answer is opened, or the open one is
  // superseded — otherwise the reviewer keeps typing into text that no longer
  // exists and the save comes back 409. Adjusting during render rather than in
  // an effect: an effect renders the stale draft once first, which is visible
  // as a flash of the previous answer's text in the box.
  const editorKey = `${proposalId}:${query.data?.version ?? ""}`;
  const [lastKey, setLastKey] = React.useState(editorKey);
  if (lastKey !== editorKey) {
    setLastKey(editorKey);
    setDraft(null);
  }

  if (query.isPending) {
    return (
      <div className="flex flex-col gap-3 p-4">
        <Skeleton className="h-5 w-1/3" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  if (query.isError || !query.data) {
    return (
      <p role="alert" className="p-4 text-sm text-unverified">
        {t(reviewErrorKey(query.error))}
      </p>
    );
  }

  const proposal = query.data;
  const editing = draft !== null;
  const displayText = proposal.final_text;

  return (
    <div className="flex flex-col gap-4 p-4">
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs text-muted-foreground">
            {proposal.requirement_ref}
          </span>
          <StatusBadge status={proposal.status} />
          <GroundingBadge verdict={proposal.grounding_verdict} />
          {proposal.is_mandatory && <Badge variant="outline">{t("mandatory")}</Badge>}
          {proposal.was_edited_by_human && <Badge variant="secondary">{t("edited")}</Badge>}
        </div>
        <p dir="auto" className="bidi-isolate text-sm">
          {proposal.requirement_text}
        </p>
      </header>

      {proposal.status === "abstained" && (
        <div className="rounded-md border border-abstained/40 bg-abstained-bg/50 p-3 text-xs">
          {/* The reason, verbatim from the backend. An abstention with no
              stated cause is the thing a reviewer skims past. */}
          <p className="font-medium">{t("abstained")}</p>
          <p className="mt-1 text-muted-foreground">{proposal.abstention_reason}</p>
        </div>
      )}

      {editing ? (
        <div className="flex flex-col gap-2">
          <textarea
            dir="auto"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={10}
            className="w-full resize-y rounded-md border border-border bg-background p-3 text-sm leading-relaxed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={!draft.trim() || edit.isPending}
              onClick={() =>
                edit.mutate(
                  {
                    proposalId: proposal.id,
                    editedText: draft,
                    expectedVersion: proposal.version,
                  },
                  { onSuccess: () => setDraft(null) },
                )
              }
            >
              {edit.isPending && <Loader2 className="animate-spin" aria-hidden />}
              {t("saveEdit")}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setDraft(null)}>
              {t("cancel")}
            </Button>
          </div>
          {proposal.status === "approved" && (
            // Saying so before the click, not after: the reviewer is about to
            // revoke a sign-off that may be someone else's.
            <p className="text-xs text-partial">{t("editRevokesApproval")}</p>
          )}
          {edit.isError && (
            <p role="alert" className="text-xs text-unverified">
              {t(reviewErrorKey(edit.error))}
            </p>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {displayText ? (
            <p dir="auto" className="bidi-isolate whitespace-pre-wrap text-sm leading-relaxed">
              {displayText}
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">{t("noAnswerYet")}</p>
          )}
          <div className="flex flex-wrap gap-2">
            {proposal.status !== "exported" && (
              <Button
                size="sm"
                variant={displayText ? "outline" : "default"}
                disabled={generate.isPending}
                onClick={() =>
                  generate.mutate({
                    requirementRef: proposal.requirement_ref,
                    requirementText: proposal.requirement_text,
                    sectionPath: proposal.section_path,
                    isMandatory: proposal.is_mandatory,
                  })
                }
              >
                {generate.isPending ? (
                  <Loader2 className="animate-spin" aria-hidden />
                ) : (
                  <Sparkles aria-hidden />
                )}
                {displayText ? t("regenerate") : t("generate")}
              </Button>
            )}
            <Button size="sm" variant="ghost" onClick={() => setDraft(displayText ?? "")}>
              <Pencil aria-hidden />
              {displayText ? t("edit") : t("writeAnswer")}
            </Button>
            {proposal.was_edited_by_human && proposal.answer_text && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setDraft(proposal.answer_text ?? "")}
                title={t("restoreGeneratedHint")}
              >
                <RotateCcw aria-hidden />
                {t("restoreGenerated")}
              </Button>
            )}
          </div>
          {generate.isError && (
            <p role="alert" className="text-xs text-unverified">
              {t(reviewErrorKey(generate.error))}
            </p>
          )}
        </div>
      )}

      {proposal.citations.length > 0 && (
        <section className="flex flex-col gap-2">
          <h4 className="text-xs font-medium text-muted-foreground">
            {t("sources", { count: proposal.citations.length })}
          </h4>
          <div className="flex flex-wrap gap-1.5">
            {proposal.citations.map((citation, index) => (
              <CitationChip
                key={`${citation.chunk_id}-${index}`}
                citation={citation}
                index={index}
                active={activeCitation?.chunk_id === citation.chunk_id}
                onSelect={onSelectCitation}
              />
            ))}
          </div>
        </section>
      )}

      <ReviewActions proposal={proposal} workspaceId={workspaceId} canApprove={canApprove} />
    </div>
  );
}
