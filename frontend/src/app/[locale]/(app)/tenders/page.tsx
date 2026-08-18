import { setRequestLocale } from "next-intl/server";

import { TenderList } from "@/components/features/tenders/tender-list";

export default async function TendersPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <TenderList />;
}
