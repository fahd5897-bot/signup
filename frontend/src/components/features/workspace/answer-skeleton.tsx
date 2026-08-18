import { Sparkles } from "lucide-react";
import { useTranslations } from "next-intl";

import { Skeleton } from "@/components/ui/skeleton";

/**
 * Loading state for a generation in flight.
 *
 * Shaped like the answer it replaces — prose lines, then a sources row — so the
 * card does not jump when the real content lands. A centred spinner would be
 * less work and would reflow the whole list on every arrival, which is
 * disorienting when four answers are streaming in at once during auto-fill.
 */
export function AnswerSkeleton() {
  const t = useTranslations("workspace");

  return (
    <div className="p-4" aria-live="polite" aria-busy="true">
      <span className="sr-only">{t("autofilling")}</span>

      <div className="mb-3 flex items-center gap-2 text-xs text-muted-foreground">
        <Sparkles className="size-3.5 animate-pulse" aria-hidden />
        <span>{t("autofilling")}</span>
      </div>

      <div className="space-y-2">
        <Skeleton className="h-3 w-full" />
        <Skeleton className="h-3 w-[94%]" />
        <Skeleton className="h-3 w-[88%]" />
        <Skeleton className="h-3 w-[62%]" />
      </div>

      {/* Placeholder source chips — the row exists in the real answer, so
          reserving it keeps the approve/reject controls from shifting. */}
      <div className="mt-4 flex gap-1.5">
        <Skeleton className="h-6 w-40 rounded-md" />
        <Skeleton className="h-6 w-32 rounded-md" />
      </div>
    </div>
  );
}
