import { getTranslations, setRequestLocale } from "next-intl/server";

import { DocumentLibrary } from "@/components/features/knowledge-base/document-library";
import { Dropzone } from "@/components/features/upload/dropzone";

/**
 * The tenant's reusable corpus: certificates, capability statements, past
 * responses — the material every answer is grounded in.
 *
 * The table is client-fetched with the session cookie. Rendering it on the
 * Next server would mean forwarding that cookie and caching one customer's
 * document list in a shared render cache.
 */
export default async function KnowledgeBasePage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations("knowledgeBase");

  return (
    <div className="h-full overflow-y-auto scrollbar-thin">
      <div className="mx-auto flex max-w-5xl flex-col gap-6 p-6">
        <header className="space-y-1">
          <h2 className="text-xl font-semibold tracking-tight">{t("title")}</h2>
          {/* max-w on prose: full-width body text at this size is genuinely
              harder to read, and worse in Arabic than in English. */}
          <p className="max-w-2xl text-sm text-muted-foreground">{t("subtitle")}</p>
        </header>

        <Dropzone role="knowledge_base" />
        <DocumentLibrary role="knowledge_base" />
      </div>
    </div>
  );
}
