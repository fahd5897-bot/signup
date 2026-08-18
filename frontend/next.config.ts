import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

export default withNextIntl({
  reactStrictMode: true,
  // Compile-time checking of Link hrefs. Moved out of `experimental` in
  // Next 16 — the old location still works but warns on every build.
  typedRoutes: true,
});
