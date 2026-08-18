"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as React from "react";

import { ApiRequestError } from "@/lib/api/client";

/**
 * Never retry a request the server has already judged.
 *
 * The default exponential retry is right for a flaky network and wrong for a
 * 4xx: re-sending a 415 (unsupported file type) or a 401 cannot change the
 * answer, and on the upload path it means re-transmitting a 200 MB tender pack
 * three times to earn the same rejection.
 */
function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiRequestError) {
    if (error.status >= 400 && error.status < 500) return false;
    // 429 is a 4xx but is explicitly worth waiting out.
    if (error.status === 429) return failureCount < 3;
  }
  return failureCount < 2;
}

export function QueryProvider({ children }: { children: React.ReactNode }) {
  // Created in state, not at module scope: a module-level client is shared
  // across requests on the server and would leak one user's cached data into
  // another's render.
  const [client] = React.useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: shouldRetry,
            staleTime: 30_000,
            refetchOnWindowFocus: false,
          },
          mutations: { retry: shouldRetry },
        },
      }),
  );

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
