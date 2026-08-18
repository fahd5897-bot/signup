import {
  CheckCircle2,
  CircleDashed,
  FileCheck2,
  MinusCircle,
  UserRoundSearch,
  XCircle,
} from "lucide-react";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import type { ProposalStatus } from "@/lib/api/types";

/**
 * Where one answer sits in the review state machine.
 *
 * Approved and exported are deliberately different: an exported answer has left
 * the building and can no longer be re-reviewed, and a reviewer who cannot see
 * that difference will try to change one and be told no with no explanation.
 */
const CONFIG = {
  draft: { variant: "outline", Icon: CircleDashed },
  abstained: { variant: "abstained", Icon: MinusCircle },
  pending_review: { variant: "partial", Icon: CircleDashed },
  needs_sme: { variant: "partial", Icon: UserRoundSearch },
  rejected: { variant: "unverified", Icon: XCircle },
  approved: { variant: "verified", Icon: CheckCircle2 },
  exported: { variant: "secondary", Icon: FileCheck2 },
} as const;

export function StatusBadge({ status }: { status: ProposalStatus }) {
  const t = useTranslations("review.status");
  const { variant, Icon } = CONFIG[status];

  return (
    <Badge variant={variant}>
      <Icon aria-hidden />
      {t(status)}
    </Badge>
  );
}
