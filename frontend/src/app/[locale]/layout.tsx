import type { Metadata } from "next";
import { Inter, IBM_Plex_Sans_Arabic } from "next/font/google";
import { NextIntlClientProvider, hasLocale } from "next-intl";
import { notFound } from "next/navigation";
import { setRequestLocale } from "next-intl/server";

import { localeDirection, routing, type Locale } from "@/i18n/routing";
import "@/styles/globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

/*
 * Self-hosted via next/font rather than a CDN link: font CDNs are unreliable
 * in several target markets, and the fallback stack renders Arabic numerals
 * inconsistently — which is unacceptable when the numbers are tender prices.
 */
const arabic = IBM_Plex_Sans_Arabic({
  subsets: ["arabic"],
  weight: ["400", "500", "600"],
  variable: "--font-arabic",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Tender Studio",
  description: "Grounded RFP, RFQ, and tender responses with verifiable citations.",
};

export function generateStaticParams() {
  return routing.locales.map((locale) => ({ locale }));
}

export default async function LocaleLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) notFound();

  // Enables static rendering for this locale's segment.
  setRequestLocale(locale);

  const dir = localeDirection[locale as Locale];

  return (
    // `dir` is decided here, on the server, and rendered into the initial HTML.
    // Setting it client-side after hydration produces a visible LTR-to-RTL flip
    // on every page load, which is the single most obvious sign of a bolted-on
    // Arabic mode.
    <html lang={locale} dir={dir} suppressHydrationWarning>
      <body className={`${inter.variable} ${arabic.variable} font-sans antialiased`}>
        <NextIntlClientProvider>{children}</NextIntlClientProvider>
      </body>
    </html>
  );
}
