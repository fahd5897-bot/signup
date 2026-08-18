import createMiddleware from "next-intl/middleware";
import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { routing } from "@/i18n/routing";

const intl = createMiddleware(routing);

/** Paths reachable without a session, after the locale prefix is stripped. */
const PUBLIC_PATHS = ["/login", "/register", "/"];

/**
 * Locale negotiation, then a session gate.
 *
 * The gate checks only that a session cookie is *present* — it does not verify
 * the token. Verification belongs to the API, which holds the signing key and
 * is the only place an authorisation decision actually matters. Doing crypto
 * here would duplicate that authority in a second place and invite the two to
 * disagree; treating this as a redirect hint keeps one source of truth.
 *
 * A forged cookie therefore reaches the app shell and then gets 401s from every
 * request it makes. That is the correct outcome: the UI is not the boundary.
 */
export default function proxy(request: NextRequest) {
  const response = intl(request);

  const { pathname } = request.nextUrl;
  const withoutLocale = pathname.replace(/^\/(en|ar)(?=\/|$)/, "") || "/";

  if (PUBLIC_PATHS.includes(withoutLocale)) return response;

  if (!request.cookies.get("access_token")) {
    const locale = pathname.match(/^\/(en|ar)(?=\/|$)/)?.[1] ?? routing.defaultLocale;
    const login = new URL(`/${locale}/login`, request.url);
    // Preserve the destination so signing in returns the user where they were
    // headed rather than dumping them on the dashboard.
    login.searchParams.set("next", pathname);
    return NextResponse.redirect(login);
  }

  return response;
}

export const config = {
  // Everything except API routes, Next internals, and files with an extension.
  matcher: ["/((?!api|_next|_vercel|.*\\..*).*)"],
};
