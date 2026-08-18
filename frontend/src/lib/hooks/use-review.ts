"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiRequestError, api } from "@/lib/api/client";
import type { Proposal, ReviewAction } from "@/lib/api/types";

/**
 * The review workbench's data layer.
 *
 * The one thing worth reading carefully is version handling. Every write sends
 * the version the reviewer's screen was showing, and the server refuses with
 * 409 rather than merging — so after any successful write the queue and the
 * open answer are both invalidated. Skipping that leaves the next action
 * carrying a stale version, and the reviewer sees a conflict they did not
 * cause.
 */

export const reviewKeys = {
  queue: (workspaceId: string) => ["review-queue", workspaceId] as const,
  progress: (workspaceId: string) => ["review-progress", workspaceId] as const,
  proposal: (proposalId: string) => ["proposal", proposalId] as const,
  exportPreview: (workspaceId: string) => ["export-preview", workspaceId] as const,
};

export function useReviewQueue(workspaceId: string) {
  return useQuery({
    queryKey: reviewKeys.queue(workspaceId),
    queryFn: () => api.reviewQueue(workspaceId),
    enabled: Boolean(workspaceId),
  });
}

export function useReviewProgress(workspaceId: string) {
  return useQuery({
    queryKey: reviewKeys.progress(workspaceId),
    queryFn: () => api.reviewProgress(workspaceId),
    enabled: Boolean(workspaceId),
  });
}

export function useProposal(proposalId: string | null) {
  return useQuery({
    queryKey: reviewKeys.proposal(proposalId ?? ""),
    queryFn: () => api.proposal(proposalId as string),
    enabled: Boolean(proposalId),
  });
}

/** Invalidate everything a write can move: the row, the queue, the gate. */
function useRefreshAfterWrite(workspaceId: string) {
  const client = useQueryClient();
  return (proposal: Proposal) => {
    client.setQueryData(reviewKeys.proposal(proposal.id), proposal);
    void client.invalidateQueries({ queryKey: reviewKeys.queue(workspaceId) });
    void client.invalidateQueries({ queryKey: reviewKeys.progress(workspaceId) });
    void client.invalidateQueries({ queryKey: reviewKeys.exportPreview(workspaceId) });
  };
}

export function useReviewAction(workspaceId: string) {
  const refresh = useRefreshAfterWrite(workspaceId);
  return useMutation({
    mutationFn: (input: {
      proposalId: string;
      action: ReviewAction;
      expectedVersion: number;
      reviewNotes?: string;
      assignedSmeId?: string;
      acknowledgeUngrounded?: boolean;
    }) =>
      api.reviewProposal(input.proposalId, {
        action: input.action,
        expected_version: input.expectedVersion,
        review_notes: input.reviewNotes ?? null,
        assigned_sme_id: input.assignedSmeId ?? null,
        acknowledge_ungrounded: input.acknowledgeUngrounded ?? false,
      }),
    onSuccess: refresh,
  });
}

export function useEditProposal(workspaceId: string) {
  const refresh = useRefreshAfterWrite(workspaceId);
  return useMutation({
    mutationFn: (input: {
      proposalId: string;
      editedText: string;
      expectedVersion: number;
      reviewNotes?: string;
    }) =>
      api.editProposal(input.proposalId, {
        edited_text: input.editedText,
        expected_version: input.expectedVersion,
        review_notes: input.reviewNotes ?? null,
      }),
    onSuccess: refresh,
  });
}

/**
 * Draft an answer for one requirement that is already on the matrix.
 *
 * Passing `workspaceId` is what makes the result persist: the endpoint saves a
 * new version and supersedes the empty row, so the answer survives a refresh
 * and can be reviewed. Without it the same call is a scratch query and nothing
 * is stored.
 */
export function useGenerateForRequirement(workspaceId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      requirementRef: string;
      requirementText: string;
      sectionPath?: string | null;
      isMandatory: boolean;
    }) =>
      api.generateAnswer(
        {
          requirement_ref: input.requirementRef,
          requirement_text: input.requirementText,
          section_path: input.sectionPath ?? null,
          is_mandatory: input.isMandatory,
        },
        workspaceId,
      ),
    onSuccess: (answer) => {
      if (answer.proposal_id) {
        void client.invalidateQueries({ queryKey: reviewKeys.proposal(answer.proposal_id) });
      }
      void client.invalidateQueries({ queryKey: reviewKeys.queue(workspaceId) });
      void client.invalidateQueries({ queryKey: reviewKeys.progress(workspaceId) });
    },
  });
}

export function useExtractRequirements(workspaceId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { documentId: string; overwriteExisting?: boolean }) =>
      api.extractRequirements(workspaceId, {
        document_id: input.documentId,
        overwrite_existing: input.overwriteExisting ?? false,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: reviewKeys.queue(workspaceId) });
      void client.invalidateQueries({ queryKey: reviewKeys.progress(workspaceId) });
    },
  });
}

/**
 * Message key for a failed review write.
 *
 * Mapping the backend's stable slug to a translation key rather than showing
 * its English `detail` — the reviewer may be working in Arabic, and a raw
 * server string is the one part of the UI that would silently stay untranslated.
 */
export function reviewErrorKey(error: unknown): string {
  if (!(error instanceof ApiRequestError)) return "errors.unknown";
  switch (error.slug) {
    case "version_conflict":
      return "errors.versionConflict";
    case "invalid_transition":
      return "errors.invalidTransition";
    case "approval_blocked":
      return "errors.approvalBlocked";
    case "permission_denied":
      return "errors.permissionDenied";
    case "not_found":
      return "errors.notFound";
    case "export_blocked":
      return "errors.exportBlocked";
    case "unauthenticated":
      return "errors.unauthenticated";
    default:
      return "errors.unknown";
  }
}
