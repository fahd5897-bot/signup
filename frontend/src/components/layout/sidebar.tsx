"use client";

import {
  ChevronsLeft,
  FileStack,
  LayoutGrid,
  Scale,
  Settings,
  type LucideIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Link, usePathname } from "@/i18n/navigation";
import { cn } from "@/lib/utils/cn";
import { Button } from "@/components/ui/button";

interface NavItem {
  href: string;
  labelKey: "knowledgeBase" | "tenders" | "goNoGo" | "settings";
  icon: LucideIcon;
}

const NAV_ITEMS: NavItem[] = [
  { href: "/knowledge-base", labelKey: "knowledgeBase", icon: FileStack },
  { href: "/tenders", labelKey: "tenders", icon: LayoutGrid },
  { href: "/go-no-go", labelKey: "goNoGo", icon: Scale },
  { href: "/settings", labelKey: "settings", icon: Settings },
];

export function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const t = useTranslations("nav");
  const pathname = usePathname();

  return (
    <aside
      className={cn(
        "flex h-full shrink-0 flex-col border-e border-border bg-card transition-[width] duration-200",
        collapsed ? "w-16" : "w-60",
      )}
    >
      <div className="flex h-14 items-center gap-2.5 px-4">
        <div className="grid size-8 shrink-0 place-items-center rounded-md bg-primary text-primary-foreground">
          <span className="text-sm font-semibold">R</span>
        </div>
        {!collapsed && (
          <span className="truncate text-sm font-semibold tracking-tight">
            Tender Studio
          </span>
        )}
      </div>

      <nav className="flex flex-1 flex-col gap-0.5 p-2" aria-label={t("workspace")}>
        {NAV_ITEMS.map((item) => {
          // Prefix match so /tenders/{id}/... keeps Tenders highlighted. The
          // pathname from next-intl already has the locale stripped.
          const active =
            pathname === item.href || pathname.startsWith(`${item.href}/`);
          const Icon = item.icon;

          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              title={collapsed ? t(item.labelKey) : undefined}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                active
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground",
                collapsed && "justify-center px-0",
              )}
            >
              <Icon className="size-4 shrink-0" aria-hidden />
              {!collapsed && <span className="truncate">{t(item.labelKey)}</span>}
            </Link>
          );
        })}
      </nav>

      <div className="p-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={onToggle}
          className={cn("w-full text-muted-foreground", collapsed && "px-0")}
          aria-label="Toggle sidebar"
        >
          {/* Logical rotation: the chevron must point outward in both
              directions, and `rtl:rotate-180` is what makes that automatic. */}
          <ChevronsLeft
            className={cn("size-4 transition-transform rtl:rotate-180", collapsed && "rotate-180")}
            aria-hidden
          />
        </Button>
      </div>
    </aside>
  );
}
