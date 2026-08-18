"use client";

import * as React from "react";

import { Sidebar } from "@/components/layout/sidebar";
import { Topbar } from "@/components/layout/topbar";
import type { Locale } from "@/i18n/routing";

/**
 * The authenticated application frame.
 *
 * Owns the one piece of chrome state (sidebar collapse) and nothing else, so
 * page content stays server-rendered where it can be. The whole frame is
 * `h-screen` with `overflow-hidden`, because the tender workspace scrolls its
 * two panes independently and a page-level scrollbar would fight them.
 */
export function AppShell({
  locale,
  title,
  children,
}: {
  locale: Locale;
  title?: string;
  children: React.ReactNode;
}) {
  const [collapsed, setCollapsed] = React.useState(false);

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <Sidebar collapsed={collapsed} onToggle={() => setCollapsed((v) => !v)} />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar locale={locale} title={title} />
        <main className="min-h-0 flex-1 overflow-hidden">{children}</main>
      </div>
    </div>
  );
}
