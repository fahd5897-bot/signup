"use client";

import { Loader2, Sparkles } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import {
  RequirementCard,
  type AnswerState,
} from "@/components/features/workspace/requirement-card";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { ApiRequestError, api } from "@/lib/api/client";
import type { Citation, Requirement } from "@/lib/api/types";

/** Concurrent generations during auto-fill.
 *
 *  Sequential would take minutes on a 40-item questionnaire; unbounded would
 *  hit the API rate limit and turn a slow fill into a failed one. Four is
 *  comfortably inside a standard tier and keeps the UI updating steadily. */
const CONCURRENCY = 4;

export function QuestionnairePanel({
  workspaceId,
  requirements,
  activeCitation,
  onSelectCitation,
}: {
  workspaceId: string;
  requirements: Requirement[];
  activeCitation: Citation | null;
  onSelectCitation: (citation: Citation | null) => void;
}) {
  const t = useTranslations("workspace");
  const [states, setStates] = React.useState<Record<string, AnswerState>>({});
  const [isAutofilling, setIsAutofilling] = React.useState(false);

  const setState = React.useCallback((ref: string, state: AnswerState) => {
    setStates((prev) => ({ ...prev, [ref]: state }));
  }, []);

  const generateOne = React.useCallback(
    async (requirement: Requirement) => {
      setState(requirement.ref, { kind: "generating" });
      try {
        const answer = await api.generateAnswer(
          {
            requirement_ref: requirement.ref,
            requirement_text: requirement.text,
            section_path: requirement.section_path,
            is_mandatory: requirement.is_mandatory,
          },
          workspaceId,
        );
        setState(requirement.ref, { kind: "answered", answer });
      } catch (error) {
        const message =
          error instanceof ApiRequestError
            ? (error.detail ?? error.message)
            : "Generation failed";
        setState(requirement.ref, { kind: "error", message });
      }
    },
    [workspaceId, setState],
  );

  const autofill = React.useCallback(async () => {
    setIsAutofilling(true);
    // Only fill what has no answer yet, so re-running after fixing a document
    // does not discard a reviewer's already-checked work.
    const pending = requirements.filter((r) => !states[r.ref]);
    const queue = [...pending];

    const workers = Array.from({ length: Math.min(CONCURRENCY, queue.length) }, async () => {
      while (queue.length > 0) {
        const next = queue.shift();
        if (next) await generateOne(next);
      }
    });

    await Promise.all(workers);
    setIsAutofilling(false);
  }, [requirements, states, generateOne]);

  const answered = Object.values(states).filter((s) => s.kind === "answered").length;
  const total = requirements.length;

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-border bg-card px-4">
        <div className="flex min-w-0 items-baseline gap-2">
          <h3 className="truncate text-sm font-medium">{t("questionnaire")}</h3>
          <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
            {answered}/{total}
          </span>
        </div>

        <Button size="sm" onClick={() => void autofill()} disabled={isAutofilling}>
          {isAutofilling ? (
            <Loader2 className="animate-spin" aria-hidden />
          ) : (
            <Sparkles aria-hidden />
          )}
          {isAutofilling ? t("autofilling") : t("autofill")}
        </Button>
      </div>

      {isAutofilling && (
        <Progress
          value={total ? (answered / total) * 100 : 0}
          className="h-0.5 rounded-none"
        />
      )}

      <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin">
        <div className="flex flex-col gap-3 p-4">
          {requirements.map((requirement) => (
            <RequirementCard
              key={requirement.ref}
              requirement={requirement}
              state={states[requirement.ref] ?? { kind: "idle" }}
              activeCitation={activeCitation}
              onGenerate={(r) => void generateOne(r)}
              onSelectCitation={onSelectCitation}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
