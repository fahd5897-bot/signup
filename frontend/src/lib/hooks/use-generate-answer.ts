"use client";

import { useMutation } from "@tanstack/react-query";
import * as React from "react";

import { ApiRequestError, api } from "@/lib/api/client";
import type { GeneratedAnswer, Requirement } from "@/lib/api/types";

export type AnswerState =
  | { kind: "idle" }
  | { kind: "generating" }
  | { kind: "answered"; answer: GeneratedAnswer }
  | { kind: "error"; message: string; slug: string };

/** Concurrent generations during auto-fill.
 *
 *  Sequential takes minutes on a 40-item questionnaire; unbounded trips the API
 *  rate limit and turns a slow fill into a failed one. Four sits comfortably
 *  inside a standard tier while keeping the UI visibly progressing. */
const CONCURRENCY = 4;

/**
 * Generates grounded answers for a questionnaire.
 *
 * Answers are keyed by requirement ref in local state rather than the query
 * cache: they are the output of a mutation, not a cacheable read, and the
 * reviewer's in-progress work must survive a refetch of anything else on the
 * page. React Query supplies the request lifecycle and the no-retry-on-4xx
 * policy — a 402 quota error re-sent three times is three billing events.
 */
export function useGenerateAnswer(workspaceId: string) {
  const [states, setStates] = React.useState<Record<string, AnswerState>>({});
  const [isAutofilling, setIsAutofilling] = React.useState(false);

  const setState = React.useCallback((ref: string, state: AnswerState) => {
    setStates((prev) => ({ ...prev, [ref]: state }));
  }, []);

  const mutation = useMutation({
    mutationFn: (requirement: Requirement) =>
      api.generateAnswer(
        {
          requirement_ref: requirement.ref,
          requirement_text: requirement.text,
          section_path: requirement.section_path,
          is_mandatory: requirement.is_mandatory,
        },
        workspaceId,
      ),
  });

  const generate = React.useCallback(
    async (requirement: Requirement) => {
      setState(requirement.ref, { kind: "generating" });
      try {
        const answer = await mutation.mutateAsync(requirement);
        setState(requirement.ref, { kind: "answered", answer });
        return answer;
      } catch (error) {
        setState(requirement.ref, {
          kind: "error",
          slug: error instanceof ApiRequestError ? error.slug : "unknown_error",
          message:
            error instanceof ApiRequestError
              ? (error.detail ?? error.message)
              : "Generation failed",
        });
        return null;
      }
    },
    [mutation, setState],
  );

  const autofill = React.useCallback(
    async (requirements: Requirement[]) => {
      setIsAutofilling(true);
      // Only fill what has no answer yet, so re-running after fixing a source
      // document never discards work a reviewer has already checked.
      const queue = requirements.filter((r) => {
        const state = states[r.ref];
        return !state || state.kind === "idle" || state.kind === "error";
      });

      const workers = Array.from(
        { length: Math.min(CONCURRENCY, queue.length) },
        async () => {
          for (let next = queue.shift(); next; next = queue.shift()) {
            await generate(next);
          }
        },
      );

      await Promise.all(workers);
      setIsAutofilling(false);
    },
    [states, generate],
  );

  const answeredCount = React.useMemo(
    () => Object.values(states).filter((s) => s.kind === "answered").length,
    [states],
  );

  return { states, generate, autofill, isAutofilling, answeredCount };
}
