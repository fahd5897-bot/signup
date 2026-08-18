"use client";

import { usePathname, useRouter } from "next/navigation";
import * as React from "react";

import { useSession } from "@/lib/hooks/use-session";

/**
 * Sends the user to sign in once the session is genuinely gone.
 *
 * The route guard in `proxy.ts` only checks that a session cookie is *present*
 * — deliberately, because verification belongs to the API. That leaves one
 * case it cannot catch: an access cookie that is still there but no longer
 * valid, with a refresh token that has also expired. The API client tries a
 * refresh first, so reaching here means that failed too.
 *
 * Without this the user sits inside the app shell with every panel reporting
 * an expired session and no control that does anything about it. Reloading
 * does not help either, because the cookie the guard looks for is still in
 * the jar.
 */
export function SessionGuard() {
  const { isUnauthenticated } = useSession();
  const router = useRouter();
  const pathname = usePathname();

  React.useEffect(() => {
    if (!isUnauthenticated) return;
    // `next` so signing in returns them where they were, rather than dumping
    // them on the dashboard mid-review.
    const locale = /^\/(en|ar)(?=\/|$)/.exec(pathname)?.[1] ?? "en";
    router.replace(`/${locale}/login?next=${encodeURIComponent(pathname)}`);
  }, [isUnauthenticated, pathname, router]);

  return null;
}
