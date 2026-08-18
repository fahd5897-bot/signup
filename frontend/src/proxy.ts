import createMiddleware from "next-intl/middleware";

import { routing } from "@/i18n/routing";

/**
 * Locale negotiation.
 *
 * Named `proxy.ts`, not `middleware.ts`: Next 16 renamed the convention and
 * warns on every build for the old name. The handler signature is unchanged,
 * so next-intl's `createMiddleware` still supplies it directly.
 */
export default createMiddleware(routing);

export const config = {
  // Everything except API routes, Next internals, and files with an extension.
  matcher: ["/((?!api|_next|_vercel|.*\\..*).*)"],
};
