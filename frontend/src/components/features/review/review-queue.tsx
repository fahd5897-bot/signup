"use client";

import { useTranslations } from "next-intl";

import { StatusBadge } from "@/components/features/review/status-badge";
import { Skeleton } from "@/components/ui/skeleton";
import type { ProposalSummary } from "@/lib/api/types";
import { cn } from "@/lib/utils/cn";
import { reviewErrorKey, useReviewQueue } from "@/lib/hooks/use-review";

/**
 * The compliance matrix as a work list.
 *
 * Ordered by requirement reference, which is the order the tender itself is
 * written in. Sorting by confidence would put the model's opinion ahead of the
 * document's structure and make a skipped clause impossible to notice.
 */
export function ReviewQueue({
  workspaceId,
  selectedId,
  onSelect,
}: {
  workspaceId: string;
  selectedId: string | null;
  onSelect: (proposal: ProposalSummary) => void;
}) {
  const t = useTranslations("review");
  const query = useReviewQueue(workspaceId);

  if (query.isPending) {
    return (
      <div className="flex flex-col gap-2 p-3">
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    );
  }

  if (query.isError) {
    return (
      <p role="alert" className="p-4 text-sm text-unverified">
        {t(reviewErrorKey(query.error))}
      </p>
    );
  }

  const items = query.data?.items ?? [];
  if (items.length === 0) {
    return <p className="p-4 text-sm text-muted-foreground">{t("emptyQueue")}</p>;
  }

  return (
    <ul className="flex flex-col">
      {items.map((item) => (
        <li key={item.id}>
          <button
            type="button"
            onClick={() => onSelect(item)}
            aria-current={item.id === selectedId}
            className={cn(
              "flex w-full flex-col gap-1.5 border-b border-border px-3 py-2.5 text-start transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
              item.id === selectedId ? "bg-primary/10" : "hover:bg-secondary/60",
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate font-mono text-xs">{item.requirement_ref}</span>
              <StatusBadge status={item.status} />
            </div>
            <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
              {item.is_mandatory && <span>{t("mandatory")}</span>}
              {/* Zero citations is the number that matters. An answer with no
                  evidence cannot be approved without a written override, and a
                  reviewer should see that before opening it. */}
              <span className={cn("tabular-nums", item.citation_count === 0 && "text-partial")}>
                {t("citationCount", { count: item.citation_count })}
              </span>
            </div>
          </button>
        </li>
      ))}
    </ul>
  );
}
