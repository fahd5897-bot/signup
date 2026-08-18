import { createNavigation } from "next-intl/navigation";

import { routing } from "@/i18n/routing";

/**
 * Locale-aware navigation primitives. Import `Link` from here, never from
 * `next/link` — the bare one drops the locale prefix and silently sends an
 * Arabic user back to the English tree.
 */
export const { Link, redirect, usePathname, useRouter, getPathname } =
  createNavigation(routing);
