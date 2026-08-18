import { getTranslations, setRequestLocale } from "next-intl/server";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default async function SettingsPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations("nav");

  return (
    <div className="h-full overflow-y-auto scrollbar-thin">
      <div className="mx-auto flex max-w-3xl flex-col gap-5 p-6">
        <h2 className="text-xl font-semibold tracking-tight">{t("settings")}</h2>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Grounding policy</CardTitle>
            <CardDescription>
              Thresholds are snapshotted onto each tender when it is created, so
              changing them here never alters confidence figures a reviewer has
              already signed off on.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Minimum evidence score</span>
              <span className="tabular-nums">0.35</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Minimum citation coverage</span>
              <span className="tabular-nums">90%</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Human approval required</span>
              <span>Always</span>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
