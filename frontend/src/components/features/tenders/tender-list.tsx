"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Calendar, Loader2, Plus } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Link } from "@/i18n/navigation";
import { api } from "@/lib/api/client";
import type { Workspace } from "@/lib/api/types";
import { reviewErrorKey } from "@/lib/hooks/use-review";

const workspaceKeys = { list: ["workspaces"] as const };

/**
 * The tender list, and the only place a pursuit can be started.
 *
 * Client-rendered rather than server-rendered: every row is tenant data
 * fetched with the session cookie, and rendering it on the Next server would
 * mean forwarding that cookie and caching one customer's pursuits in a shared
 * render cache.
 */
export function TenderList() {
  const t = useTranslations("tenders");
  const client = useQueryClient();
  const [creating, setCreating] = React.useState(false);

  const query = useQuery({ queryKey: workspaceKeys.list, queryFn: () => api.listWorkspaces() });
  const create = useMutation({
    mutationFn: (body: { name: string; tender_reference?: string; response_language: string }) =>
      api.createWorkspace(body),
    onSuccess: () => {
      setCreating(false);
      void client.invalidateQueries({ queryKey: workspaceKeys.list });
    },
  });

  return (
    <div className="h-full overflow-y-auto scrollbar-thin">
      <div className="mx-auto flex max-w-5xl flex-col gap-5 p-6">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-xl font-semibold tracking-tight">{t("title")}</h2>
          {!creating && (
            <Button size="sm" onClick={() => setCreating(true)}>
              <Plus aria-hidden />
              {t("new")}
            </Button>
          )}
        </div>

        {creating && (
          <NewTenderForm
            pending={create.isPending}
            error={create.isError ? t(reviewErrorKey(create.error)) : null}
            onCancel={() => setCreating(false)}
            onSubmit={(body) => create.mutate(body)}
          />
        )}

        {query.isPending ? (
          <div className="flex flex-col gap-3">
            {Array.from({ length: 3 }, (_, i) => (
              <Skeleton key={i} className="h-20 w-full" />
            ))}
          </div>
        ) : query.isError ? (
          <p role="alert" className="text-sm text-unverified">
            {t(reviewErrorKey(query.error))}
          </p>
        ) : (query.data?.items.length ?? 0) === 0 ? (
          <div className="rounded-lg border border-dashed border-border px-6 py-12 text-center">
            <p className="text-sm font-medium">{t("empty")}</p>
            <p className="mt-1 text-sm text-muted-foreground">{t("emptyHint")}</p>
          </div>
        ) : (
          <ul className="flex flex-col gap-3">
            {query.data?.items.map((workspace) => (
              <li key={workspace.id}>
                <TenderRow workspace={workspace} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function TenderRow({ workspace }: { workspace: Workspace }) {
  const t = useTranslations("tenders");
  const percent = workspace.completion_ratio * 100;

  return (
    <Link
      href={`/tenders/${workspace.id}`}
      className="group flex items-center gap-4 rounded-lg border border-border bg-card p-4 transition-colors hover:border-primary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          {/* `dir="auto"` on customer-supplied text: a tender name is Arabic or
              English and the layout cannot know which. */}
          <span dir="auto" className="bidi-isolate truncate text-sm font-medium">
            {workspace.name}
          </span>
          <Badge variant={workspace.is_exportable ? "verified" : "secondary"}>
            {workspace.requirements_approved}/{workspace.requirements_total}
          </Badge>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          {workspace.tender_reference && (
            <span dir="auto" className="bidi-isolate font-mono">
              {workspace.tender_reference}
            </span>
          )}
          {workspace.submission_deadline && (
            <span className="inline-flex items-center gap-1">
              <Calendar className="size-3" aria-hidden />
              {workspace.submission_deadline}
            </span>
          )}
          <span>{t("mandatoryOnly")}</span>
        </div>
        <Progress value={percent} className="mt-2 h-1" />
      </div>
      {/* Flipped in RTL, or the affordance points away from the destination. */}
      <ArrowRight className="size-4 shrink-0 text-muted-foreground rtl:-scale-x-100" aria-hidden />
    </Link>
  );
}

function NewTenderForm({
  pending,
  error,
  onCancel,
  onSubmit,
}: {
  pending: boolean;
  error: string | null;
  onCancel: () => void;
  onSubmit: (body: {
    name: string;
    tender_reference?: string;
    response_language: string;
  }) => void;
}) {
  const t = useTranslations("tenders");
  const [name, setName] = React.useState("");
  const [reference, setReference] = React.useState("");
  const [language, setLanguage] = React.useState("en");

  return (
    <form
      className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit({
          name: name.trim(),
          tender_reference: reference.trim() || undefined,
          response_language: language,
        });
      }}
    >
      <Field id="tender-name" label={t("name")}>
        <Input value={name} dir="auto" onChange={(e) => setName(e.target.value)} required />
      </Field>
      <Field id="tender-reference" label={t("reference")}>
        <Input
          value={reference}
          dir="auto"
          onChange={(e) => setReference(e.target.value)}
          placeholder="MOH/2026/IT/0114"
        />
      </Field>
      <Field id="tender-language" label={t("responseLanguage")}>
        {/* The language the response is written in, which is not necessarily
            the language of the tender, and not the reviewer's UI locale. */}
        <select
          value={language}
          onChange={(e) => setLanguage(e.target.value)}
          className="h-9 w-full rounded-md border border-border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <option value="en">English</option>
          <option value="ar">العربية</option>
        </select>
      </Field>

      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={pending || name.trim().length < 2}>
          {pending && <Loader2 className="animate-spin" aria-hidden />}
          {t("create")}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          {t("cancel")}
        </Button>
      </div>

      {error && (
        <p role="alert" className="text-xs text-unverified">
          {error}
        </p>
      )}
    </form>
  );
}
