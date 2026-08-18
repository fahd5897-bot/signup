"use client";

import { Globe } from "lucide-react";
import { useTranslations } from "next-intl";

import { Link, usePathname } from "@/i18n/navigation";
import { Button } from "@/components/ui/button";
import type { Locale } from "@/i18n/routing";

/** Route prefix -> nav translation key, longest match wins. */
const TITLE_KEYS = [
  ["/knowledge-base", "knowledgeBase"],
  ["/go-no-go", "goNoGo"],
  ["/settings", "settings"],
  ["/tenders", "tenders"],
] as const;

export function Topbar({ locale, title }: { locale: Locale; title?: string }) {
  const t = useTranslations("nav");
  const pathname = usePathname();
  const other: Locale = locale === "ar" ? "en" : "ar";

  // Derived from the route rather than threaded down from each page, so a new
  // page cannot ship with the wrong heading by forgetting to pass one.
  const matched = TITLE_KEYS.find(
    ([prefix]) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
  const heading = title ?? (matched ? t(matched[1]) : t("workspace"));

  return (
    <header className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-border bg-card px-5">
      <h1 className="truncate text-sm font-semibold">{heading}</h1>

      <div className="flex items-center gap-2">
        {/*
          Switching locale preserves the current path, so a reviewer mid-way
          through a tender does not get bounced to the dashboard. UI language
          is independent of the language a response is written in.
        */}
        <Button variant="ghost" size="sm" asChild>
          <Link href={pathname} locale={other} className="gap-1.5">
            <Globe className="size-4" aria-hidden />
            <span className="text-xs font-medium uppercase">{other}</span>
          </Link>
        </Button>
        <div
          className="size-8 rounded-full bg-secondary text-center text-xs font-medium leading-8 text-secondary-foreground"
          aria-hidden
        >
          FA
        </div>
      </div>
    </header>
  );
}
