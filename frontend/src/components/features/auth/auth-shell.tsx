import * as React from "react";

/**
 * Centred frame for the signed-out pages.
 *
 * Deliberately plain. Someone reaching this screen is trying to get in, not to
 * be marketed at, and a hero here just delays the form.
 */
export function AuthShell({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
  footer: React.ReactNode;
}) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-6">
      <div className="w-full max-w-sm">
        <div className="mb-7 flex items-center gap-2.5">
          <div className="grid size-8 shrink-0 place-items-center rounded-md bg-primary text-primary-foreground">
            <span className="text-sm font-semibold">R</span>
          </div>
          <span className="text-sm font-semibold tracking-tight">Tender Studio</span>
        </div>

        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>

        <div className="mt-6">{children}</div>

        <p className="mt-6 text-sm text-muted-foreground">{footer}</p>
      </div>
    </div>
  );
}
