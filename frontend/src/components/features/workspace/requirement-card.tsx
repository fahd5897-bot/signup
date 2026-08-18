"use client";

import {
  AlertCircle,
  Check,
  Loader2,
  MinusCircle,
  RefreshCw,
  Sparkles,
  X,
} from "lucide-react";
import { useTranslations } from "next-intl";

import { CitationChip } from "@/components/features/citations/citation-chip";
import { GroundingBadge } from "@/components/features/citations/grounding-badge";
import { AnswerSkeleton } from "@/components/features/workspace/answer-skeleton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import type { Citation, Requirement } from "@/lib/api/types";
import type { AnswerState } from "@/lib/hooks/use-generate-answer";
import { cn } from "@/lib/utils/cn";

export type { AnswerState };

/**
 * Highlight sentences the verifier could not tie to a citation.
 *
 * Splitting on the verifier's own `unsupported_sentences` rather than
 * re-deriving them locally: the backend segments Arabic and English with the
 * same rules, and a second implementation here would disagree on exactly the
 * mixed-script text where it matters most.
 */
function renderAnswer(text: string, unsupported: string[]) {
  if (unsupported.length === 0) return text;

  const escaped = unsupported.map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const parts = text.split(new RegExp(`(${escaped.join("|")})`, "g"));

  return parts.map((part, i) =>
    unsupported.includes(part) ? (
      <mark
        key={i}
        className="rounded-sm bg-partial-bg px-0.5 text-partial decoration-partial/50 decoration-wavy underline-offset-4"
        style={{ textDecorationLine: "underline" }}
      >
        {part}
      </mark>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}

export function RequirementCard({
  requirement,
  state,
  activeCitation,
  onGenerate,
  onSelectCitation,
}: {
  requirement: Requirement;
  state: AnswerState;
  activeCitation: Citation | null;
  onGenerate: (requirement: Requirement) => void;
  onSelectCitation: (citation: Citation | null) => void;
}) {
  const t = useTranslations("workspace");
  const tG = useTranslations("grounding");

  const answer = state.kind === "answered" ? state.answer : null;
  const abstained = answer?.status === "abstained";

  return (
    <article
      id={`req-${requirement.ref}`}
      className="scroll-mt-4 rounded-lg border border-border bg-card"
    >
      <div className="flex items-start gap-3 p-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs font-semibold tabular-nums text-muted-foreground">
              {requirement.ref}
            </span>
            {requirement.is_mandatory && (
              <Badge variant="outline" className="text-[10px] uppercase tracking-wide">
                {t("mandatory")}
              </Badge>
            )}
            {answer && (
              <GroundingBadge
                verdict={answer.grounding.verdict}
                coverage={answer.grounding.citation_coverage}
              />
            )}
          </div>
          {/* dir="auto" — an Arabic clause in an English UI must still read RTL,
              or its trailing punctuation jumps to the wrong end. */}
          <p dir="auto" className="bidi-isolate mt-1.5 text-sm leading-relaxed">
            {requirement.text}
          </p>
          {requirement.section_path && (
            <p dir="auto" className="bidi-isolate mt-1 truncate text-xs text-muted-foreground">
              {requirement.section_path}
            </p>
          )}
        </div>

        <Button
          variant={answer ? "ghost" : "outline"}
          size="sm"
          className="shrink-0"
          onClick={() => onGenerate(requirement)}
          disabled={state.kind === "generating"}
        >
          {state.kind === "generating" ? (
            <Loader2 className="animate-spin" aria-hidden />
          ) : answer ? (
            <RefreshCw aria-hidden />
          ) : (
            <Sparkles aria-hidden />
          )}
          <span className="sr-only sm:not-sr-only">
            {answer ? t("regenerate") : t("generate")}
          </span>
        </Button>
      </div>

      {state.kind === "generating" && (
        <>
          <Separator />
          <AnswerSkeleton />
        </>
      )}

      {state.kind === "error" && (
        <>
          <Separator />
          <div className="flex items-start gap-2.5 p-4">
            <AlertCircle className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="text-sm text-destructive">{state.message}</p>
              {/* Quota is the one failure a reviewer can act on themselves;
                  everything else is a retry or an admin problem. */}
              {state.slug === "quota_exceeded" && (
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Your plan\u2019s generation quota is exhausted.
                </p>
              )}
            </div>
            <Button
              variant="ghost"
              size="sm"
              className="shrink-0"
              onClick={() => onGenerate(requirement)}
            >
              <RefreshCw aria-hidden />
              {t("regenerate")}
            </Button>
          </div>
        </>
      )}

      {answer && (
        <>
          <Separator />
          <div className="p-4">
            {abstained ? (
              /*
                An abstention shows the reason and no answer text, mirroring the
                backend, which stores none. A plausible-looking placeholder is
                what a reviewer skims past and approves; an explicit gap is
                something they have to act on.
              */
              <div className="flex items-start gap-2.5 rounded-md border border-border bg-muted/40 p-3">
                <MinusCircle className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
                <div className="min-w-0">
                  <p className="text-sm font-medium">{tG("abstained")}</p>
                  {answer.abstention_reason && (
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {answer.abstention_reason}
                    </p>
                  )}
                </div>
              </div>
            ) : (
              <>
                <div
                  // The answer's language is a property of the response, not of
                  // the UI — an Arabic reviewer may be checking an English bid.
                  lang={answer.language === "ar" ? "ar" : "en"}
                  dir={answer.language === "ar" ? "rtl" : "ltr"}
                  className="bidi-isolate whitespace-pre-wrap text-sm leading-relaxed"
                >
                  {renderAnswer(
                    answer.answer_text ?? "",
                    answer.grounding.unsupported_sentences,
                  )}
                </div>

                {answer.grounding.unsupported_sentences.length > 0 && (
                  <div className="mt-3 rounded-md border border-partial/30 bg-partial-bg/50 p-3">
                    <p className="text-xs font-medium text-partial">
                      {tG("unsupportedTitle")}
                    </p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {tG("unsupportedHint")}
                    </p>
                  </div>
                )}

                {answer.citations.length > 0 && (
                  <div className="mt-3">
                    <p className="mb-1.5 text-xs font-medium text-muted-foreground">
                      {t("sources")}
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      {answer.citations.map((citation, i) => (
                        <CitationChip
                          key={`${citation.chunk_id}-${i}`}
                          citation={citation}
                          index={i}
                          active={activeCitation?.chunk_id === citation.chunk_id}
                          onSelect={(c) =>
                            onSelectCitation(
                              activeCitation?.chunk_id === c.chunk_id ? null : c,
                            )
                          }
                        />
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}

            <div className="mt-4 flex items-center gap-2">
              {/*
                Approve is blocked for UNVERIFIED and for abstentions — there is
                nothing a reviewer can responsibly sign off there.

                PARTIAL stays approvable on purpose: the unsupported sentences
                are flagged inline above, and the reviewer's job is to verify or
                remove them and then approve. Blocking it would leave no path
                through the workflow except regenerating and hoping.

                The backend enforces the real invariant regardless — approval
                requires a named reviewer (a CHECK constraint on the proposals
                table) and export is blocked while any mandatory requirement is
                unapproved. This is a guardrail, not the guarantee.
              */}
              <Button
                size="sm"
                disabled={abstained || answer.grounding.verdict === "unverified"}
              >
                <Check aria-hidden />
                {t("approve")}
              </Button>
              <Button variant="ghost" size="sm">
                <X aria-hidden />
                {t("reject")}
              </Button>

              {answer.grounding.confidence_score !== null && (
                <span
                  className={cn(
                    "ms-auto text-xs tabular-nums",
                    answer.grounding.confidence_score >= 0.8
                      ? "text-muted-foreground"
                      : "text-partial",
                  )}
                >
                  {tG("confidence")}{" "}
                  {Math.round(answer.grounding.confidence_score * 100)}%
                </span>
              )}
            </div>
          </div>
        </>
      )}
    </article>
  );
}
