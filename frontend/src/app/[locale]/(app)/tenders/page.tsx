import { ArrowRight, Calendar } from "lucide-react";
import { getTranslations, setRequestLocale } from "next-intl/server";

import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Link } from "@/i18n/navigation";

const TENDERS = [
  {
    id: "a1",
    name: "Ministry of Health — Core Network Refresh",
    reference: "MOH/2026/IT/0114",
    deadline: "2026-09-14",
    approved: 18,
    total: 42,
    status: "in_review",
  },
  {
    id: "b2",
    name: "أمانة منطقة الرياض — أنظمة المراقبة",
    reference: "RIY/2026/SEC/0088",
    deadline: "2026-08-30",
    approved: 41,
    total: 41,
    status: "approved",
  },
];

export default async function TendersPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations("nav");

  return (
    <div className="h-full overflow-y-auto scrollbar-thin">
      <div className="mx-auto flex max-w-5xl flex-col gap-5 p-6">
        <h2 className="text-xl font-semibold tracking-tight">{t("tenders")}</h2>

        <ul className="flex flex-col gap-3">
          {TENDERS.map((tender) => (
            <li key={tender.id}>
              <Link
                href={`/tenders/${tender.id}`}
                className="group flex items-center gap-4 rounded-lg border border-border bg-card p-4 transition-colors hover:border-primary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span dir="auto" className="bidi-isolate truncate text-sm font-medium">
                      {tender.name}
                    </span>
                    <Badge variant={tender.status === "approved" ? "verified" : "secondary"}>
                      {tender.approved}/{tender.total}
                    </Badge>
                  </div>
                  <div className="mt-1 flex items-center gap-3 text-xs text-muted-foreground">
                    <span className="font-mono">{tender.reference}</span>
                    <span className="inline-flex items-center gap-1">
                      <Calendar className="size-3" aria-hidden />
                      <time dateTime={tender.deadline}>{tender.deadline}</time>
                    </span>
                  </div>
                  <Progress
                    value={(tender.approved / tender.total) * 100}
                    className="mt-2.5 max-w-xs"
                    indicatorClassName={
                      tender.approved === tender.total ? "bg-verified" : undefined
                    }
                  />
                </div>
                <ArrowRight
                  className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5 rtl:rotate-180 rtl:group-hover:-translate-x-0.5"
                  aria-hidden
                />
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
