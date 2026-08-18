import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

export default withNextIntl({
  reactStrictMode: true,
  // Emits .next/standalone: a self-contained server with only the modules the
  // app actually imports. The container image is built from it, so without
  // this the Docker build fails on a missing path rather than shipping a
  // bloated image.
  output: "standalone",
  // Compile-time checking of Link hrefs. Moved out of `experimental` in
  // Next 16 — the old location still works but warns on every build.
  typedRoutes: true,
});
