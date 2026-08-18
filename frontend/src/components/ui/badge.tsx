import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils/cn";

/**
 * Grounding-state variants carry the product's core signal, so they are
 * first-class here rather than ad-hoc classes at each call site — a reviewer
 * learns four colours once and reads them everywhere.
 */
const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium transition-colors [&_svg]:size-3 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary/10 text-primary",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        outline: "border-border text-muted-foreground",
        verified: "border-transparent bg-verified-bg text-verified",
        partial: "border-transparent bg-partial-bg text-partial",
        unverified: "border-transparent bg-unverified-bg text-unverified",
        abstained: "border-transparent bg-abstained-bg text-abstained",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
