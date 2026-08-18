import { AlertTriangle, CheckCircle2, MinusCircle, XCircle } from "lucide-react";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import type { GroundingVerdict } from "@/lib/api/types";

const CONFIG = {
  verified: { variant: "verified", Icon: CheckCircle2 },
  partial: { variant: "partial", Icon: AlertTriangle },
  unverified: { variant: "unverified", Icon: XCircle },
  not_applicable: { variant: "abstained", Icon: MinusCircle },
} as const;

/**
 * The verdict from the backend's grounding verifier, shown verbatim.
 *
 * Never derived client-side from citation count: coverage is computed by
 * `app/rag/grounding/verifier.py` against sentence-level analysis, and a
 * cheaper local approximation would disagree with it — leaving two different
 * "is this grounded?" answers on screen.
 */
export function GroundingBadge({
  verdict,
  coverage,
}: {
  verdict: GroundingVerdict;
  coverage?: number | null;
}) {
  const t = useTranslations("grounding");
  const { variant, Icon } = CONFIG[verdict];

  return (
    <Badge variant={variant}>
      <Icon aria-hidden />
      {t(verdict)}
      {coverage !== null && coverage !== undefined && verdict !== "not_applicable" && (
        <span className="tabular-nums opacity-80">{Math.round(coverage * 100)}%</span>
      )}
    </Badge>
  );
}
