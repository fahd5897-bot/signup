"use client";

import { Loader2, Sparkles } from "lucide-react";
import { useTranslations } from "next-intl";

import { RequirementCard } from "@/components/features/workspace/requirement-card";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import type { Citation, Requirement } from "@/lib/api/types";
import { useGenerateAnswer } from "@/lib/hooks/use-generate-answer";

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

  // All request lifecycle, concurrency, and retry policy live in the hook; this
  // component only renders. That split is what lets the auto-fill behaviour be
  // tested without mounting the panel.
  const { states, generate, autofill, isAutofilling, answeredCount } =
    useGenerateAnswer(workspaceId);

  const total = requirements.length;

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-border bg-card px-4">
        <div className="flex min-w-0 items-baseline gap-2">
          <h3 className="truncate text-sm font-medium">{t("questionnaire")}</h3>
          <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
            {answeredCount}/{total}
          </span>
        </div>

        <Button
          size="sm"
          onClick={() => void autofill(requirements)}
          disabled={isAutofilling}
        >
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
          value={total ? (answeredCount / total) * 100 : 0}
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
              onGenerate={(r) => void generate(r)}
              onSelectCitation={onSelectCitation}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
