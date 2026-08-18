# GenAI RFP / RFQ / Tender Response Platform — System Architecture

**Status:** Task 1 — structure, dependencies, and data flow. No application logic yet.

---

## 0. Stack decisions that differ from the brief

Three corrections were made before scaffolding. Each is load-bearing.

| Brief said | Reality | Why it changes |
|---|---|---|
| Claude 3.5 Sonnet | **`claude-opus-5`** for generation, **`claude-haiku-4-5`** for extraction/classification | Claude 3.5 Sonnet is retired. Current IDs are `claude-opus-5` ($5/$25 per MTok, 1M ctx), `claude-sonnet-5` ($3/$15), `claude-haiku-4-5` ($1/$5, 200K ctx). Opus 5 also unlocks adaptive thinking and effort control, which directly serve the accuracy constraint. |
| "LangChain + Claude for RAG" | **The native Anthropic SDK owns the whole path.** LangChain is pinned but currently unused — see the note below | Anthropic's document `citations` feature returns `cited_text` plus exact `char_location` / `page_location` per claim, computed server-side. That is the single strongest anti-hallucination primitive available, and it is not exposed through the LangChain chat abstraction. See ADR-0001. |
| Qdrant for embeddings | Qdrant, but **embeddings come from Voyage** (`voyage-multilingual-2`) | Anthropic ships no embedding model. Voyage is the Anthropic-recommended partner and is genuinely strong on Arabic. `bge-m3` self-hosted is the data-residency fallback (some GCC tenders require in-country processing). |

**On LangChain.** The brief named it, and it is pinned in `requirements.txt`, but
no module imports it today: the chains in `rag/chains/` are plain async Python
calling the Anthropic SDK. That happened because every stage that LangChain
would have orchestrated needs something the chat abstraction hides — the
`search_result` content blocks that produce server-computed citations, the
`thinking` and `effort` controls the abstention judgement depends on, and the
exact `stop_reason` the verifier branches on. Orchestrating four steps in
Python turned out to be less code than working around that.

It is left in the pins rather than removed because dropping a dependency the
brief asked for is the customer's call, not ours. If it stays unused, removing
it and its transitive tree (langgraph, langsmith) is a worthwhile cut: they are
a large share of install time and image size for code that never runs.

LangChain 1.x is a breaking rewrite of 0.x — most tutorials you will find are
for 0.x and will not run. Pins are exact for that reason.

---

## 1. Backend — FastAPI folder structure

```
backend/
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml                    # ruff + mypy(strict) + pytest config
├── alembic.ini
│
├── app/
│   ├── main.py                       # app factory, lifespan, router mounting
│   │
│   ├── core/
│   │   ├── config.py                 # pydantic-settings, env-typed
│   │   ├── logging.py                # structlog, tenant_id in every record
│   │   ├── exceptions.py             # UngroundedAnswerError, TenantMismatchError
│   │   └── constants.py
│   │
│   ├── api/
│   │   ├── middleware/
│   │   │   ├── tenant_context.py     # JWT -> ContextVar[tenant_id]  ← see §5
│   │   │   ├── request_id.py
│   │   │   └── rate_limit.py         # slowapi, per-tenant quota
│   │   └── v1/
│   │       ├── deps/
│   │       │   ├── auth.py           # current_user, require_role
│   │       │   ├── db.py             # session bound to tenant RLS
│   │       │   └── tenancy.py        # resolve + assert tenant
│   │       └── routers/
│   │           ├── auth.py           # login, refresh, OIDC/SSO callback
│   │           ├── tenants.py        # org CRUD, seats, quotas
│   │           ├── documents.py      # upload, status, presigned URLs
│   │           ├── knowledge_base.py # the reusable corpus (past bids, specs)
│   │           ├── tenders.py        # an RFP/RFQ being responded to
│   │           ├── requirements.py   # extracted compliance-matrix rows
│   │           ├── generation.py     # draft answers (SSE streaming)
│   │           ├── review.py         # human-in-the-loop approve/reject/edit
│   │           ├── exports.py        # DOCX/PDF response pack
│   │           └── health.py
│   │
│   ├── schemas/                      # Pydantic v2 request/response contracts
│   │   ├── document.py
│   │   ├── requirement.py
│   │   ├── answer.py                 # Answer + Citation + confidence
│   │   ├── citation.py
│   │   └── review.py
│   │
│   ├── db/
│   │   ├── session.py                # async engine; SET LOCAL app.tenant_id
│   │   ├── base.py
│   │   ├── models/                   # SQLAlchemy 2.0 ORM
│   │   │   ├── tenant.py
│   │   │   ├── user.py
│   │   │   ├── document.py           # source file + parse status
│   │   │   ├── chunk.py              # chunk metadata mirror of Qdrant point
│   │   │   ├── tender.py
│   │   │   ├── requirement.py
│   │   │   ├── answer.py
│   │   │   ├── citation.py
│   │   │   ├── review_action.py      # immutable HITL audit trail
│   │   │   └── audit_log.py
│   │   ├── repositories/             # data access; no business rules
│   │   └── migrations/versions/      # Alembic; RLS policies live here
│   │
│   ├── ingestion/                    # ── upload -> retrievable chunks ──
│   │   ├── pipeline.py               # orchestrates the stages below
│   │   ├── parsers/
│   │   │   ├── unstructured_parser.py# hi_res strategy, ara+eng OCR
│   │   │   ├── pdf_probe.py          # PyMuPDF: born-digital vs scanned
│   │   │   ├── excel_parser.py       # BoQ / pricing sheets -> atomic tables
│   │   │   └── table_extractor.py    # tables -> HTML, never flattened
│   │   ├── normalizers/
│   │   │   ├── arabic.py             # dediacritise, tatweel, alef/ya unify
│   │   │   ├── bidi.py               # presentation-form -> logical order
│   │   │   └── language_detect.py    # per-block ar/en tagging
│   │   └── chunking/
│   │       ├── semantic_chunker.py   # title-aware, respects heading tree
│   │       ├── table_chunker.py      # 1 table = 1 chunk, never split
│   │       └── metadata.py           # page, section, bbox -> citation anchors
│   │
│   ├── rag/                          # ── retrieval + grounded generation ──
│   │   ├── embeddings/
│   │   │   ├── voyage.py
│   │   │   └── sparse.py             # fastembed BM25/miniCOIL sparse vectors
│   │   ├── vectorstore/
│   │   │   ├── qdrant_client.py
│   │   │   ├── collections.py        # schema + tenant payload index
│   │   │   └── filters.py            # MANDATORY tenant filter builder
│   │   ├── retrieval/
│   │   │   ├── hybrid.py             # dense + sparse, RRF fusion
│   │   │   ├── reranker.py           # Voyage rerank-2, cross-encoder
│   │   │   └── query_expansion.py    # ar<->en bilingual expansion
│   │   ├── grounding/                # ── the zero-hallucination core ──
│   │   │   ├── context_builder.py    # chunks -> document blocks
│   │   │   ├── citation_mapper.py    # API char offsets -> page/bbox
│   │   │   ├── verifier.py           # 2nd-pass claim<->evidence check
│   │   │   ├── coverage.py           # % of sentences carrying a citation
│   │   │   └── abstention.py         # fail-closed policy gate
│   │   ├── prompts/
│   │   │   ├── system_en.py
│   │   │   ├── system_ar.py
│   │   │   ├── requirement_extraction.py
│   │   │   └── answer_generation.py
│   │   ├── chains/                   # plain async orchestration (see note above)
│   │   │   ├── extract_requirements.py
│   │   │   ├── answer_requirement.py
│   │   │   └── compliance_matrix.py
│   │   └── evaluation/
│   │       ├── golden_set.py         # curated ar/en Q->answer+cite fixtures
│   │       ├── metrics.py            # groundedness, citation precision/recall
│   │       └── regression.py         # CI gate: block deploy on score drop
│   │
│   ├── services/                     # business logic, transaction boundaries
│   │   ├── document_service.py
│   │   ├── tender_service.py
│   │   ├── generation_service.py
│   │   ├── review_service.py
│   │   └── quota_service.py
│   │
│   ├── security/
│   │   ├── jwt.py
│   │   ├── rbac.py                   # owner / bid_manager / sme / viewer
│   │   ├── pii.py                    # Presidio scrub before LLM egress
│   │   └── encryption.py             # per-tenant envelope keys at rest
│   │
│   ├── workers/                      # Celery — all heavy work is async
│   │   ├── celery_app.py
│   │   └── tasks/
│   │       ├── ingest.py             # parse -> chunk -> embed -> upsert
│   │       ├── extract.py            # RFP -> requirement rows
│   │       ├── generate.py           # batch draft all answers
│   │       └── export.py
│   │
│   ├── exporters/
│   │   ├── docx_exporter.py          # docxtpl, tenant-branded, RTL sections
│   │   ├── pdf_exporter.py           # WeasyPrint
│   │   └── compliance_matrix.py      # XLSX traceability matrix
│   │
│   └── observability/
│       ├── tracing.py                # OpenTelemetry
│       ├── llm_tracing.py            # Langfuse: prompt, ctx, cites, verdict
│       └── metrics.py                # Prometheus
│
├── tests/{unit,integration,e2e,fixtures/documents}
├── scripts/                          # seed, reindex, backfill, eval-run
└── docker/
```

---

## 2. Frontend — Next.js (App Router) folder structure

```
frontend/
├── package.json  next.config.ts  tsconfig.json  postcss.config.mjs
├── components.json                   # shadcn/ui generator config
│
├── messages/
│   ├── en.json
│   └── ar.json                       # next-intl message catalogues
│
├── public/fonts/                     # IBM Plex Sans Arabic (self-hosted)
│
└── src/
    ├── middleware.ts                 # locale negotiation + auth guard
    │
    ├── i18n/
    │   ├── routing.ts                # locales: ['en','ar'], prefix strategy
    │   └── request.ts                # per-request message loading
    │
    ├── app/
    │   ├── [locale]/
    │   │   ├── layout.tsx            # sets <html lang dir="rtl|ltr">  ← §6
    │   │   ├── (marketing)/          # public pages
    │   │   ├── (auth)/
    │   │   │   ├── login/
    │   │   │   └── register/
    │   │   └── (app)/                # authenticated shell
    │   │       ├── layout.tsx        # sidebar, tenant switcher
    │   │       ├── dashboard/
    │   │       ├── tenders/
    │   │       │   └── [tenderId]/
    │   │       │       ├── page.tsx          # overview + progress
    │   │       │       ├── requirements/     # extracted compliance matrix
    │   │       │       ├── review/           # ← the HITL workbench
    │   │       │       └── export/
    │   │       ├── knowledge-base/   # corpus mgmt, re-index, coverage
    │   │       └── settings/         # members, branding, quotas, locale
    │   │
    │   └── api/auth/[...nextauth]/   # NextAuth route handler
    │
    ├── components/
    │   ├── ui/                       # shadcn primitives (generated)
    │   ├── layout/
    │   └── features/
    │       ├── upload/               # dropzone, parse-status poller
    │       ├── requirements/         # matrix table, bulk actions
    │       ├── answer-editor/        # draft editor, regenerate, tone
    │       ├── citations/            # ← citation chip -> PDF page+highlight
    │       ├── review/               # approve / reject / request-SME
    │       └── knowledge-base/
    │
    ├── lib/
    │   ├── api/                      # typed fetch client, SSE stream reader
    │   ├── hooks/                    # react-query hooks
    │   ├── stores/                   # zustand (editor + review UI state)
    │   ├── validation/               # zod schemas mirroring Pydantic
    │   └── utils/
    │
    ├── styles/globals.css            # Tailwind v4 @theme, logical properties
    └── types/
```

**Two structural notes.** The `[locale]` segment wraps everything so `dir` is decided at the server layout — not flipped client-side after paint. And `citations/` is a first-class feature folder, not a UI detail: a citation chip must deep-link to the source page with the exact span highlighted, or reviewers cannot verify at speed, and the human-in-the-loop constraint collapses into rubber-stamping.

---

## 3. Data flow — upload to generated answer

### Phase A — Ingestion (async, Celery, minutes)

```
1. POST /v1/documents        multipart upload
2. API                       validate type/size -> presigned PUT -> S3
                             INSERT documents (status=PENDING)
                             enqueue ingest.parse_document(doc_id, tenant_id)
                             return 202 + doc_id            ← never block the request
3. Worker: PROBE             PyMuPDF — born-digital or scanned?
4. Worker: PARSE             Unstructured.io partition()
                               strategy=hi_res, ocr_languages=["ara","eng"]
                               infer_table_structure=True
                             -> typed elements (Title/NarrativeText/Table/...)
                             Tables kept as HTML. Excel BoQ sheets parsed
                             per-sheet with headers preserved.
5. Worker: NORMALISE         per-block language detect; Arabic path runs
                             BiDi reorder -> reshape -> dediacritise ->
                             alef/ya/tatweel unification.
                             Raw text is retained alongside — citations must
                             quote the ORIGINAL, never the normalised form.
6. Worker: CHUNK             title-aware semantic chunking; a table is always
                             one atomic chunk. Each chunk carries
                             {doc_id, page, section_path, bbox, lang, char_span}
                             — these become the citation anchors.
7. Worker: EMBED             voyage-multilingual-2 (dense)
                             + fastembed BM25 (sparse), batched
8. Worker: UPSERT            Qdrant single collection, named vectors
                             {dense, sparse}; payload includes tenant_id.
                             Mirror row -> Postgres chunks table.
                             status=READY (or FAILED + reason, surfaced in UI)
```

### Phase B — Tender intake

```
 9. Upload the RFP itself -> same pipeline, tagged role=TENDER
10. extract.requirements task: claude-haiku-4-5 with structured outputs
    walks the tender doc and emits compliance-matrix rows —
    {ref, text, section, mandatory: bool, response_type, deadline}
11. Rows land in `requirements`, rendered as an editable matrix.
    The bid manager confirms/edits scope BEFORE any drafting spends tokens.
```

### Phase C — Grounded generation (per requirement)

```
12. Query build      requirement text -> bilingual expansion (ar<->en), so an
                     Arabic requirement still retrieves English past bids.
13. Hybrid retrieve  Qdrant dense + sparse, RRF-fused, top-50.
                     ►► tenant_id filter is applied unconditionally in
                        filters.py — it is not a caller-supplied argument.
14. Rerank           Voyage rerank-2 cross-encoder -> top-8.
15. GATE             best score < MIN_RETRIEVAL_SCORE?
                       -> ABSTAIN. Emit "insufficient evidence", route to SME.
                          No LLM call. This is the first fail-closed door.
16. Context build    each chunk -> an Anthropic `document` content block with
                     citations: {enabled: true}. Stable tenant preamble sits
                     behind a prompt-cache breakpoint; the volatile question
                     goes last.
17. Generate         claude-opus-5, adaptive thinking, effort=high, streamed.
                     System prompt: answer ONLY from provided documents; if
                     unsupported, say so. Returns text blocks where cited
                     spans carry a `citations` array with cited_text and
                     exact char/page location — computed by the API, not
                     asked of the model, so it cannot be fabricated.
18. VERIFY           second-pass claim check: split into sentences, assert
                     each factual sentence maps to >= 1 citation resolving to
                     a real chunk owned by this tenant. Compute coverage.
19. GATE             coverage < MIN_CITATION_COVERAGE?
                       -> mark UNVERIFIED, strip unsupported sentences, flag
                          for mandatory human edit. Second fail-closed door.
20. Persist          answers + citations rows; confidence = f(retrieval score,
                     rerank score, coverage, verifier verdict).
                     status=DRAFT — never AUTO-APPROVED.
```

### Phase D — Human-in-the-loop, then export

```
21. Review workbench: answer on the left, cited source page rendered on the
    right with the span highlighted. One click to jump to the origin.
22. Reviewer approves / edits / rejects / assigns to an SME.
    Every action appends an immutable review_actions row (who, when, before,
    after) — the audit trail public tenders demand.
23. Approved + edited answers are fed back into the knowledge base as
    high-authority chunks. The corpus compounds with every won bid.
24. Export: docxtpl -> tenant-branded DOCX, WeasyPrint -> PDF, plus an XLSX
    compliance matrix mapping every requirement to its answer and sources.
    ►► Export is blocked while any mandatory requirement is unapproved.
       Third fail-closed door.
```

**The invariant across all four phases:** text only becomes a claim after it survives retrieval scoring, API-computed citations, a verifier pass, and a named human. Nothing reaches a customer's tender submission on the model's word alone.

---

## 4. Zero-hallucination: where each defence sits

| Layer | Mechanism | Fails to |
|---|---|---|
| Retrieval | score threshold before any LLM call (step 15) | abstain |
| Generation | Anthropic native citations — offsets computed server-side from the actual document bytes | unciteable text is visible as uncited |
| Verification | independent claim↔evidence pass + coverage ratio (18–19) | strip + flag |
| Tenancy | citation must resolve to a chunk owned by the caller's tenant | reject |
| Human | mandatory approval, immutable audit trail | block export |

A model that is merely *asked* to cite will invent citations. Because the offsets here are produced by the API against the supplied document bytes, a fabricated citation cannot resolve — which converts hallucination from an invisible failure into a mechanically detectable one.

---

## 5. Multi-tenancy

**PostgreSQL** — shared schema, `tenant_id` on every business table, Row-Level Security policies created in Alembic migrations. Each request opens its transaction with `SET LOCAL app.tenant_id`; policies read that GUC. The application connects as a non-`BYPASSRLS` role, so a forgotten `WHERE` clause leaks nothing.

**Qdrant** — one collection, not one per tenant. Per-tenant collections exhaust file handles and make reindexing O(tenants). Instead `tenant_id` is a payload field with a tenant-optimised keyword index, which physically co-locates each tenant's vectors on disk; the mandatory filter in `filters.py` is then both the isolation boundary and a performance win. Isolation is enforced in one construction site, and no route may build a bare Qdrant filter.

**S3** — key prefix `s3://bucket/{tenant_id}/{document_id}/`, per-tenant envelope encryption keys.

---

## 6. Arabic / English localisation

- **Direction is server-decided.** `[locale]/layout.tsx` emits `<html lang dir>` — no post-paint flip.
- **Tailwind v4 logical properties** (`ms-*`, `pe-*`, `text-start`) throughout; physical `left/right` utilities are a lint error, because they are what actually breaks RTL.
- **Self-hosted IBM Plex Sans Arabic** — CDN fonts are unreliable in several target markets, and the fallback stack mangles Arabic numerals.
- **Content language is independent of UI language.** An Arabic tender may be answered from English past bids; the answer's language follows the tender, the chrome follows the user. These are two settings, not one.
- **Normalise for retrieval, quote the original for citation.** Diacritics and alef variants are stripped for matching; the cited text a reviewer sees is the untouched source, or the highlight will not align with the PDF.

---

## 7. Architecture decision records

| ADR | Decision |
|---|---|
| 0001 | Anthropic SDK owns generation *and* orchestration — native citations, adaptive thinking, and `stop_reason` are all unavailable through the chat abstraction |
| 0002 | LangChain 1.x pinned exactly but currently unused; 0.x tutorials do not apply |
| 0003 | Stream all generation calls — 64K `max_tokens` exceeds non-streaming HTTP timeouts |
| 0004 | `tiktoken` is used for rough budgeting only; exact counts come from `messages.count_tokens` |
| 0005 | Single Qdrant collection with tenant payload index, not collection-per-tenant |

---

## 8. Task 1 deliverable status

Structure, dependency manifests, and data flow are complete. Directories are scaffolded on disk and match the trees above. No application logic has been written — that is Task 2.
