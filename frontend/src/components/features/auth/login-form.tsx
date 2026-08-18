"use client";

import { Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { useRouter } from "@/i18n/navigation";
import { authErrorKey, useLogin } from "@/lib/hooks/use-session";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function LoginForm() {
  const t = useTranslations("auth");
  const router = useRouter();
  const login = useLogin();

  const [values, setValues] = React.useState({ email: "", password: "", tenant_slug: "" });
  const [errors, setErrors] = React.useState<Record<string, string>>({});

  // The backend refuses to guess when one email belongs to several
  // organisations, so the field appears only once that has actually happened.
  const needsTenant =
    login.isError && authErrorKey(login.error) === "invalidCredentials" && values.tenant_slug === "" && login.failureCount > 1;

  function validate() {
    const next: Record<string, string> = {};
    if (!EMAIL_RE.test(values.email)) next.email = t("errors.emailInvalid");
    if (!values.password) next.password = t("errors.required");
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!validate()) return;
    try {
      await login.mutateAsync({
        email: values.email,
        password: values.password,
        ...(values.tenant_slug ? { tenant_slug: values.tenant_slug } : {}),
      });
      router.push("/knowledge-base");
    } catch {
      // Rendered from login.error below; mutateAsync rejects on failure and an
      // unhandled rejection would surface as a console error for no benefit.
    }
  }

  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
      <Field id="email" label={t("email")} error={errors.email}>
        <Input
          type="email"
          value={values.email}
          autoComplete="username"
          autoFocus
          onChange={(e) => setValues((v) => ({ ...v, email: e.target.value }))}
        />
      </Field>

      <Field id="password" label={t("password")} error={errors.password}>
        <Input
          type="password"
          value={values.password}
          autoComplete="current-password"
          onChange={(e) => setValues((v) => ({ ...v, password: e.target.value }))}
        />
      </Field>

      {needsTenant && (
        <Field id="tenant_slug" label={t("tenantSlug")} hint={t("tenantSlugHint")}>
          <Input
            value={values.tenant_slug}
            onChange={(e) => setValues((v) => ({ ...v, tenant_slug: e.target.value }))}
          />
        </Field>
      )}

      {login.isError && (
        // role="alert" so the failure is announced, not just coloured.
        <p role="alert" className="text-sm text-destructive">
          {t(`errors.${authErrorKey(login.error)}`)}
        </p>
      )}

      <Button type="submit" disabled={login.isPending} className="mt-1 w-full">
        {login.isPending && <Loader2 className="animate-spin" aria-hidden />}
        {login.isPending ? t("submitting") : t("signIn")}
      </Button>
    </form>
  );
}
