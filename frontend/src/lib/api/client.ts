import type {
  ApiError,
  AuthenticatedUser,
  DocumentStatusRead,
  GeneratedAnswer,
  TaskAccepted,
  TokenPair,
} from "@/lib/api/types";

/**
 * Base URL for the FastAPI backend.
 *
 * Defaults to a same-origin path, which is the deployment worth preferring: a
 * rewrite in front of both apps means no CORS preflight on any request and no
 * third-party-cookie exposure for the session cookie.
 *
 * Set `NEXT_PUBLIC_API_BASE_URL` for a split-origin deployment (frontend on
 * Vercel, API elsewhere). Note that `NEXT_PUBLIC_*` values are inlined at
 * BUILD time, not read at runtime — the same image cannot be promoted from
 * staging to production with a different API host, it must be rebuilt. If that
 * matters, serve the value from a runtime config endpoint instead.
 */
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1";

/** Typed error carrying the backend's stable slug, so callers branch on
 *  `slug` rather than on message text. */
export class ApiRequestError extends Error {
  constructor(
    readonly status: number,
    readonly slug: string,
    message: string,
    readonly detail?: string,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

async function parseError(response: Response): Promise<ApiRequestError> {
  let body: Partial<ApiError> = {};
  try {
    body = (await response.json()) as ApiError;
  } catch {
    // Non-JSON error (proxy timeout, gateway page). Fall through to defaults.
  }
  return new ApiRequestError(
    response.status,
    body.type ?? "unknown_error",
    body.title ?? response.statusText ?? "Request failed",
    body.detail,
  );
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...init?.headers,
    },
    // The access token is an httpOnly cookie set at login; it is never read by
    // JS, so an XSS cannot exfiltrate it.
    credentials: "include",
  });

  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  /**
   * Create an organisation and its first user, who becomes the owner.
   *
   * The response body carries tokens for non-browser clients, but the browser
   * relies on the httpOnly cookies the server sets alongside them — those are
   * unreadable from JavaScript, so an XSS cannot lift the session.
   */
  register(body: {
    organisation_name: string;
    full_name: string;
    email: string;
    password: string;
  }) {
    return request<TokenPair>("/auth/register", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  login(body: { email: string; password: string; tenant_slug?: string }) {
    return request<TokenPair>("/auth/login", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  logout() {
    return request<void>("/auth/logout", { method: "POST" });
  },

  /** Current user, resolved from the session cookie. */
  me() {
    return request<AuthenticatedUser>("/auth/me");
  },

  /**
   * Upload one document. Returns 202 — ingestion is asynchronous, so the
   * caller polls `poll_url` until the status is terminal.
   *
   * `onProgress` uses XHR rather than fetch because fetch still cannot report
   * upload progress, and a 200 MB tender pack with no progress bar reads as a
   * frozen page.
   */
  uploadDocument(
    file: File,
    options: {
      role: string;
      workspaceId?: string;
      onProgress?: (percent: number) => void;
      signal?: AbortSignal;
    },
  ): Promise<TaskAccepted> {
    const form = new FormData();
    form.append("file", file);
    form.append("role", options.role);
    if (options.workspaceId) form.append("workspace_id", options.workspaceId);

    return new Promise<TaskAccepted>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API_BASE}/upload-document`);
      xhr.withCredentials = true;

      xhr.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) {
          options.onProgress?.(Math.round((event.loaded / event.total) * 100));
        }
      });

      xhr.addEventListener("load", () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText) as TaskAccepted);
          return;
        }
        let body: Partial<ApiError> = {};
        try {
          body = JSON.parse(xhr.responseText) as ApiError;
        } catch {
          /* non-JSON error body */
        }
        reject(
          new ApiRequestError(
            xhr.status,
            body.type ?? "unknown_error",
            body.title ?? "Upload failed",
            body.detail,
          ),
        );
      });

      xhr.addEventListener("error", () =>
        reject(new ApiRequestError(0, "network_error", "Network error during upload")),
      );
      xhr.addEventListener("abort", () =>
        reject(new ApiRequestError(0, "aborted", "Upload cancelled")),
      );
      options.signal?.addEventListener("abort", () => xhr.abort());

      xhr.send(form);
    });
  },

  getDocumentStatus(documentId: string) {
    return request<DocumentStatusRead>(`/documents/${documentId}/status`);
  },

  /** Generate one grounded answer. Seconds, not minutes — synchronous. */
  generateAnswer(
    body: {
      requirement_ref: string;
      requirement_text: string;
      section_path?: string | null;
      is_mandatory?: boolean;
      language?: string | null;
      style_hint?: string | null;
    },
    workspaceId?: string,
  ) {
    const query = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : "";
    return request<GeneratedAnswer>(`/generate-answer${query}`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
};
