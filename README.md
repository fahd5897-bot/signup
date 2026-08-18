# RFP / RFQ / Tender Response Platform

GenAI-native, multi-tenant B2B SaaS that drafts responses to RFPs, RFQs, and
public tenders — grounded in the tenant's own document corpus, with citations
resolved against source bytes and mandatory human approval before export.
Arabic and English throughout.

| | |
|---|---|
| Frontend | Next.js 16 (App Router), Tailwind v4, shadcn/ui, next-intl |
| Backend | FastAPI, Celery |
| AI | Claude Opus 5 (generation) / Haiku 4.5 (extraction), Anthropic SDK directly |
| Data | PostgreSQL + RLS (metadata), Qdrant (vectors), Redis, S3 |
| Parsing | Unstructured.io — Arabic OCR + table structure inference |

**Read [`ARCHITECTURE.md`](./ARCHITECTURE.md) first.** It carries the folder
structure, the data flow from upload to approved answer, and the
fail-closed gates that implement the zero-hallucination constraint.

## Running it

There is no hosted URL — nothing is deployed. See
**[QUICKSTART.md](./QUICKSTART.md)**; the whole stack comes up with Docker, or
the interface alone with Node in about two minutes.

```bash
cp .env.example .env                 # fill ANTHROPIC_API_KEY + VOYAGE_API_KEY + JWT_SECRET
docker compose up -d db redis qdrant minio minio-init
docker compose run --rm api alembic upgrade head
docker compose up -d
```

## The one rule

No answer is issued without a citation resolving to a real source in the
tenant's own documents, and nothing is exported without a named human having
approved it. Five gates enforce it, and each one fails closed:

| Gate | Refuses when | Where |
|---|---|---|
| Evidence | the best retrieved chunk scores below the threshold | `rag/chains/answer_requirement.py` |
| Grounding | the model answered outside its sources | `rag/prompts/answer_generation.py` |
| Citation | a returned citation does not resolve to a supplied chunk | `rag/grounding/citation_mapper.py` |
| Coverage | too few claim sentences carry a resolving citation | `rag/grounding/verifier.py` |
| Approval | a human has not signed off, by name, on the exact text | `services/review_service.py` |

Requirement extraction runs the same way in reverse: a requirement is recorded
only if its verbatim source span is found in the tender.

## Status

Working end to end: registration and login, tenant isolation enforced by
PostgreSQL row-level security, Arabic/English ingestion, requirement extraction
into a compliance matrix, grounded generation with citations, human review and
approval, and export to DOCX, PDF, and the compliance matrix.

QUICKSTART.md carries the full built/not-built table.
