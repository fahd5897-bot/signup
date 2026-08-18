"use client";

import { Check, Loader2, UserRoundSearch, X } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import type { Proposal, ReviewAction } from "@/lib/api/types";
import { reviewErrorKey, useReviewAction } from "@/lib/hooks/use-review";

/**
 * The approval gate, as the reviewer meets it.
 *
 * Two behaviours here mirror server rules rather than duplicating them. The
 * uncited-approval acknowledgement and the rejection note are *required* by the
 * API; asking for them here means the reviewer is told what is needed before
 * they click, instead of after a 422. The server still refuses without them —
 * this form is a courtesy, never the control.
 */
export function ReviewActions({
  proposal,
  workspaceId,
  canApprove,
}: {
  proposal: Proposal;
  workspaceId: string;
  canApprove: boolean;
}) {
  const t = useTranslations("review");
  const action = useReviewAction(workspaceId);

  const [notes, setNotes] = React.useState("");
  const [acknowledged, setAcknowledged] = React.useState(false);

  const isUncited = proposal.citations.length === 0;
  const isTerminal = proposal.status === "exported";
  const hasText = Boolean(proposal.final_text?.trim());

  // Mirrors `TRANSITIONS` in review_service.py. Kept narrow: offering a button
  // the server will refuse teaches reviewers to distrust the interface.
  const allowed: Record<string, ReviewAction[]> = {
    draft: ["pending_review", "needs_sme", "approved", "rejected"],
    abstained: ["pending_review", "needs_sme", "rejected"],
    pending_review: ["approved", "rejected", "needs_sme"],
    needs_sme: ["pending_review", "approved", "rejected"],
    rejected: ["pending_review", "needs_sme"],
    approved: ["rejected", "pending_review", "needs_sme"],
    exported: [],
  };
  const available = allowed[proposal.status] ?? [];

  const submit = (chosen: ReviewAction) =>
    action.mutate({
      proposalId: proposal.id,
      action: chosen,
      expectedVersion: proposal.version,
      reviewNotes: notes.trim() || undefined,
      acknowledgeUngrounded: chosen === "approved" ? acknowledged : undefined,
    });

  if (isTerminal) {
    return <p className="text-xs text-muted-foreground">{t("exportedLocked")}</p>;
  }

  const approveBlocked =
    !hasText || (isUncited && (!acknowledged || !notes.trim()));

  return (
    <div className="flex flex-col gap-3 border-t border-border pt-3">
      <Field id={`notes-${proposal.id}`} label={t("notes")}>
        <Input
          value={notes}
          dir="auto"
          onChange={(e) => setNotes(e.target.value)}
          placeholder={t("notesPlaceholder")}
        />
      </Field>

      {isUncited && hasText && (
        <label className="flex items-start gap-2 rounded-md border border-partial/40 bg-partial-bg/50 p-3 text-xs">
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(e) => setAcknowledged(e.target.checked)}
            className="mt-0.5 size-4 shrink-0 accent-current"
          />
          {/* The wording is deliberately first-person. This is the moment the
              responsibility for an ungrounded claim transfers to a human, and
              a passive "override grounding check" hides that. */}
          <span>{t("acknowledgeUngrounded")}</span>
        </label>
      )}

      {!hasText && <p className="text-xs text-muted-foreground">{t("nothingToApprove")}</p>}

      <div className="flex flex-wrap gap-2">
        {available.includes("approved") && (
          <Button
            size="sm"
            disabled={!canApprove || approveBlocked || action.isPending}
            onClick={() => submit("approved")}
            title={canApprove ? undefined : t("approveRequiresRole")}
          >
            {action.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <Check aria-hidden />}
            {t("approve")}
          </Button>
        )}
        {available.includes("rejected") && (
          <Button
            size="sm"
            variant="outline"
            disabled={!canApprove || !notes.trim() || action.isPending}
            onClick={() => submit("rejected")}
            title={notes.trim() ? undefined : t("rejectRequiresNotes")}
          >
            <X aria-hidden />
            {t("reject")}
          </Button>
        )}
        {available.includes("pending_review") && (
          <Button
            size="sm"
            variant="ghost"
            disabled={action.isPending}
            onClick={() => submit("pending_review")}
          >
            {t("sendToReview")}
          </Button>
        )}
        {available.includes("needs_sme") && (
          <Button size="sm" variant="ghost" disabled title={t("escalateNeedsSme")}>
            <UserRoundSearch aria-hidden />
            {t("escalate")}
          </Button>
        )}
      </div>

      {action.isError && (
        <p role="alert" className="text-xs text-unverified">
          {t(reviewErrorKey(action.error))}
        </p>
      )}
    </div>
  );
}
