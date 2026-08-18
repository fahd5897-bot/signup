/**
 * Types mirroring the FastAPI response models.
 *
 * Hand-maintained rather than generated, so the mirror is the thing to check
 * when the backend changes. The source of truth is `app/schemas/` — these
 * names and optionality match it exactly, including which fields are nullable,
 * because `answer_text: null` is a meaningful state (an abstention) and not an
 * error to code around.
 */

export type Locale = "en" | "ar";
export type Language = "en" | "ar" | "mixed" | "unknown";

export type GroundingVerdict = "verified" | "partial" | "unverified" | "not_applicable";

export type ProposalStatus =
  | "draft"
  | "abstained"
  | "pending_review"
  | "needs_sme"
  | "rejected"
  | "approved"
  | "exported";

export type DocumentStatus =
  | "pending"
  | "uploaded"
  | "parsing"
  | "chunking"
  | "embedding"
  | "ready"
  | "failed"
  | "quarantined";

export type DocumentRole = "tender" | "knowledge_base" | "past_proposal" | "attachment";

export type ChunkType =
  | "title"
  | "narrative"
  | "table"
  | "list"
  | "form_field"
  | "header_footer"
  | "image_caption";

/** One evidence span. Mirrors `app/schemas/proposal.py::Citation`. */
export interface Citation {
  chunk_id: string;
  document_id: string;
  document_name: string;
  /** 1-indexed. Null for formats without pagination (xlsx, csv). */
  page_number: number | null;
  chunk_type: ChunkType;
  /** The source text verbatim — what the highlight must match. */
  quoted_text: string;
  section_path: string | null;
  relevance_score: number | null;
}

export interface GroundingMetrics {
  verdict: GroundingVerdict;
  citation_coverage: number | null;
  top_retrieval_score: number | null;
  confidence_score: number | null;
  citation_count: number;
  /** Sentences the verifier could not tie to a citation. Highlighted inline. */
  unsupported_sentences: string[];
}

/** Response of `POST /api/v1/generate-answer`. Not yet persisted. */
export interface GeneratedAnswer {
  requirement_ref: string;
  requirement_text: string;
  /** Null on abstention — deliberately no placeholder text to rubber-stamp. */
  answer_text: string | null;
  language: Language;
  status: ProposalStatus;
  citations: Citation[];
  grounding: GroundingMetrics;
  abstention_reason: string | null;
  model_id: string | null;
  prompt_version: string | null;
  generation_ms: number | null;
  /** Set only when the answer was saved, which requires a workspace to save it
   *  to. Their absence is how the client knows the result is ephemeral. */
  proposal_id: string | null;
  version: number | null;
}

export interface DocumentSummary {
  id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  role: DocumentRole;
  status: DocumentStatus;
  language: Language;
  page_count: number | null;
  chunk_count: number;
  /** Share of pages that yielded text; the Arabic-scan quality signal. */
  text_extraction_ratio: number | null;
  extraction_quality: "good" | "degraded" | "poor" | "unknown";
  failure_reason: string | null;
  created_at: string;
}

/** Polling projection from `GET /api/v1/documents/{id}/status`. */
export interface DocumentStatusRead {
  id: string;
  status: DocumentStatus;
  chunk_count: number;
  failure_reason: string | null;
  /** Tells the client to stop polling. */
  is_terminal: boolean;
}

/** 202 body from `POST /api/v1/upload-document`. */
export interface TaskAccepted {
  task_id: string;
  resource_id: string;
  status: string;
  poll_url: string;
}

export type UserRole = "owner" | "bid_manager" | "sme" | "viewer";

/** Mirrors `app/schemas/auth.py::AuthenticatedUser`. */
export interface AuthenticatedUser {
  id: string;
  tenant_id: string;
  email: string;
  role: UserRole;
}

/** Mirrors `app/schemas/auth.py::TokenPair`. */
export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  /** Access-token lifetime in seconds. */
  expires_in: number;
  user: AuthenticatedUser;
}

/** RFC 9457-shaped error body from the global handler. */
export interface ApiError {
  type: string;
  title: string;
  detail?: string;
  errors?: { field: string; message: string }[];
}

export interface Requirement {
  ref: string;
  text: string;
  section_path: string | null;
  is_mandatory: boolean;
}

// ---------------------------------------------------------------- review

/** Row projection for the review queue. Answer bodies stay out of a 200-row
 *  list response — a full compliance matrix would otherwise be megabytes of
 *  JSON before the reviewer has opened anything. */
export interface ProposalSummary {
  id: string;
  requirement_ref: string;
  is_mandatory: boolean;
  status: ProposalStatus;
  grounding_verdict: GroundingVerdict;
  confidence_score: number | null;
  citation_count: number;
  /** Optimistic-lock token. Every write must echo it back. */
  version: number;
}

/** One answer with its full evidence. Mirrors `ProposalRead`. */
export interface Proposal {
  id: string;
  tenant_id: string;
  workspace_id: string;
  requirement_ref: string;
  requirement_text: string;
  section_path: string | null;
  is_mandatory: boolean;
  answer_text: string | null;
  edited_text: string | null;
  language: Language;
  citations: Citation[];
  grounding_verdict: GroundingVerdict;
  citation_coverage: number | null;
  top_retrieval_score: number | null;
  confidence_score: number | null;
  abstention_reason: string | null;
  status: ProposalStatus;
  reviewed_by_id: string | null;
  reviewed_at: string | null;
  review_notes: string | null;
  assigned_sme_id: string | null;
  version: number;
  is_current: boolean;
  model_id: string | null;
  /** What export would emit: the reviewer's edit, else the generated text. */
  final_text: string | null;
  was_edited_by_human: boolean;
  requires_human_edit: boolean;
  created_at: string;
  updated_at: string;
}

/** Statuses a reviewer may move an answer to. Narrower than ProposalStatus:
 *  `draft`, `abstained`, and `exported` are produced by the system, never
 *  chosen by a human. */
export type ReviewAction = "approved" | "rejected" | "needs_sme" | "pending_review";

export interface ReviewProgress {
  approved: number;
  total: number;
  outstanding: number;
  /** The only value the export endpoint consults. */
  ready_to_export: boolean;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

// ------------------------------------------------------------ requirements

export interface ExtractedRequirement {
  requirement_ref: string;
  requirement_text: string;
  /** The verbatim tender span the requirement was read from. Checked against
   *  the document during extraction, so its presence is the evidence that the
   *  requirement is genuinely in the tender. */
  source_text: string;
  is_mandatory: boolean;
  category: string;
  section_path: string | null;
  page_number: number | null;
  /** The tender did not number this item; a stable reference was assigned. */
  ref_is_synthetic: boolean;
}

export interface RequirementExtractionResult {
  document_id: string;
  workspace_id: string;
  created: number;
  skipped_existing: number;
  mandatory: number;
  /** Proposed requirements whose source span could not be found. A high count
   *  means the document is badly scanned or is being summarised rather than
   *  quoted, and the matrix should not be trusted until it is looked at. */
  dropped: number;
  windows: number;
  requirements: ExtractedRequirement[];
}

// ----------------------------------------------------------------- export

export type ExportFormat = "docx" | "pdf" | "matrix";

export interface ExportPreview {
  requirements: number;
  answered: number;
  mandatory: number;
  /** Answers going out with no citation. Each was approved by a named human
   *  who took explicit responsibility; the count is surfaced so nobody has to
   *  take that on trust. */
  uncited: number;
  exported: number;
}
