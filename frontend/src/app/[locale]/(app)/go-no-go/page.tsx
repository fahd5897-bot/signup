import { Scale } from "lucide-react";
import { getTranslations, setRequestLocale } from "next-intl/server";

/**
 * Go/No-Go Analysis — scoped but not yet built.
 *
 * This screen was introduced with the frontend brief and has no backend behind
 * it: deciding whether to bid means scoring a tender's requirements against the
 * knowledge base and reporting what the company cannot evidence, which is a
 * new analysis endpoint rather than a view over existing data. The nav entry
 * and route exist so the information architecture is settled; the placeholder
 * states what it will do rather than pretending to do it.
 */
export default async function GoNoGoPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations("nav");

  return (
    <div className="grid h-full place-items-center p-6">
      <div className="max-w-md text-center">
        <div className="mx-auto grid size-11 place-items-center rounded-full bg-secondary text-muted-foreground">
          <Scale className="size-5" aria-hidden />
        </div>
        <h2 className="mt-4 text-lg font-semibold tracking-tight">{t("goNoGo")}</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Scores an incoming tender against your knowledge base and reports which
          mandatory requirements you cannot currently evidence — before you commit
          bid team time.
        </p>
        <p className="mt-3 text-xs text-muted-foreground">
          Requires a coverage-analysis endpoint that does not exist yet.
        </p>
      </div>
    </div>
  );
}
