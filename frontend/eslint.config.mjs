import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

/**
 * Flat config, using eslint-config-next's own flat exports directly.
 * The FlatCompat shim that older Next templates use does not work under
 * ESLint 10 — it throws on a circular plugin reference during validation.
 */
export default [
  ...nextCoreWebVitals,
  ...nextTypescript,
  { ignores: [".next/**", "node_modules/**", "next-env.d.ts"] },
];
