import { setRequestLocale } from "next-intl/server";

import { AppShell } from "@/components/layout/app-shell";
import type { Locale } from "@/i18n/routing";

export default async function AppLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);

  return <AppShell locale={locale as Locale}>{children}</AppShell>;
}
