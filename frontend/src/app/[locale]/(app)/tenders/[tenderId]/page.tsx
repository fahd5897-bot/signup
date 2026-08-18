import { setRequestLocale } from "next-intl/server";

import { SplitView } from "@/components/features/workspace/split-view";

/**
 * The tender workspace.
 *
 * A shell only: everything on the page is the tenant's own data, fetched
 * client-side with the session cookie. Rendering it on the server would mean
 * forwarding the cookie through the Next server and caching a tenant's
 * compliance matrix in a shared render cache — the kind of mistake that leaks
 * one customer's tender into another's page.
 */
export default async function TenderWorkspacePage({
  params,
}: {
  params: Promise<{ locale: string; tenderId: string }>;
}) {
  const { locale, tenderId } = await params;
  setRequestLocale(locale);

  return <SplitView workspaceId={tenderId} />;
}
