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
