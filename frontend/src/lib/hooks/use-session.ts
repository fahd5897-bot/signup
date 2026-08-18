"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as React from "react";

import { ApiRequestError, api } from "@/lib/api/client";
import type { AuthenticatedUser } from "@/lib/api/types";

export const SESSION_KEY = ["session"] as const;

/**
 * The signed-in user, or null.
 *
 * Resolved by asking the server rather than by decoding a token client-side.
 * The session lives in an httpOnly cookie precisely so JavaScript cannot read
 * it — and a client that decodes its own claims is trusting a value it cannot
 * verify anyway.
 */
export function useSession() {
  const query = useQuery({
    queryKey: SESSION_KEY,
    queryFn: () => api.me(),
    // 401 is a legitimate answer ("nobody is signed in"), not a failure to
    // retry. Retrying it delays every logged-out page load by seconds.
    retry: false,
    staleTime: 60_000,
  });

  const isUnauthenticated =
    query.error instanceof ApiRequestError && query.error.status === 401;

  return {
    user: (query.data ?? null) as AuthenticatedUser | null,
    isLoading: query.isLoading,
    isAuthenticated: Boolean(query.data),
    isUnauthenticated,
    error: isUnauthenticated ? null : query.error,
  };
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.login,
    onSuccess: (pair) => {
      // Seed the cache from the login response so the first authenticated
      // render does not flash a loading state while /me round-trips.
      queryClient.setQueryData(SESSION_KEY, pair.user);
    },
  });
}

export function useRegister() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.register,
    onSuccess: (pair) => {
      queryClient.setQueryData(SESSION_KEY, pair.user);
    },
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.logout,
    onSettled: () => {
      // Clear on settled, not on success: if the request failed the cookie may
      // still be gone, and leaving stale user data cached is worse than an
      // extra sign-in.
      queryClient.setQueryData(SESSION_KEY, null);
      void queryClient.clear();
    },
  });
}

/** Maps a backend error slug to a translation key under `auth.errors`. */
export function authErrorKey(error: unknown): string {
  if (!(error instanceof ApiRequestError)) return "network";
  switch (error.slug) {
    case "invalid_credentials":
      return "invalidCredentials";
    case "email_taken":
      return "emailTaken";
    case "slug_taken":
      return "slugTaken";
    case "validation_error":
      return "validation";
    default:
      return "unknown";
  }
}

/** Focus the first field with an error, for keyboard and screen-reader users. */
export function useFocusFirstError(errors: Record<string, unknown>) {
  React.useEffect(() => {
    const first = Object.keys(errors)[0];
    if (first) document.getElementById(first)?.focus();
  }, [errors]);
}
