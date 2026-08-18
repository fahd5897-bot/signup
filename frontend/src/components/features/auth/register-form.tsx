"use client";

import { Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { useRouter } from "@/i18n/navigation";
import { authErrorKey, useRegister } from "@/lib/hooks/use-session";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
//: Mirrors MIN_PASSWORD_LENGTH in app/schemas/auth.py. Validating here is a
//: courtesy — the server is the authority and rejects short passwords too.
const MIN_PASSWORD = 12;

export function RegisterForm() {
  const t = useTranslations("auth");
  const router = useRouter();
  const register = useRegister();

  const [values, setValues] = React.useState({
    organisation_name: "",
    full_name: "",
    email: "",
    password: "",
  });
  const [errors, setErrors] = React.useState<Record<string, string>>({});

  function validate() {
    const next: Record<string, string> = {};
    if (values.organisation_name.trim().length < 2) {
      next.organisation_name = t("errors.required");
    }
    if (!values.full_name.trim()) next.full_name = t("errors.required");
    if (!EMAIL_RE.test(values.email)) next.email = t("errors.emailInvalid");
    if (values.password.length < MIN_PASSWORD) next.password = t("errors.passwordShort");
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!validate()) return;
    try {
      await register.mutateAsync(values);
      router.push("/knowledge-base");
    } catch {
      // Surfaced from register.error below.
    }
  }

  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
      <Field id="organisation_name" label={t("organisationName")} error={errors.organisation_name}>
        <Input
          value={values.organisation_name}
          autoComplete="organization"
          autoFocus
          onChange={(e) => setValues((v) => ({ ...v, organisation_name: e.target.value }))}
        />
      </Field>

      <Field id="full_name" label={t("fullName")} error={errors.full_name}>
        <Input
          value={values.full_name}
          autoComplete="name"
          onChange={(e) => setValues((v) => ({ ...v, full_name: e.target.value }))}
        />
      </Field>

      <Field id="email" label={t("email")} error={errors.email}>
        <Input
          type="email"
          value={values.email}
          autoComplete="username"
          onChange={(e) => setValues((v) => ({ ...v, email: e.target.value }))}
        />
      </Field>

      <Field
        id="password"
        label={t("password")}
        hint={t("passwordHint")}
        error={errors.password}
      >
        <Input
          type="password"
          value={values.password}
          autoComplete="new-password"
          onChange={(e) => setValues((v) => ({ ...v, password: e.target.value }))}
        />
      </Field>

      {register.isError && (
        <p role="alert" className="text-sm text-destructive">
          {t(`errors.${authErrorKey(register.error)}`)}
        </p>
      )}

      <Button type="submit" disabled={register.isPending} className="mt-1 w-full">
        {register.isPending && <Loader2 className="animate-spin" aria-hidden />}
        {register.isPending ? t("submitting") : t("signUp")}
      </Button>
    </form>
  );
}
