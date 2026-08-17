# RFP / RFQ / Tender Response Platform

GenAI-native, multi-tenant B2B SaaS that drafts responses to RFPs, RFQs, and
public tenders — grounded in the tenant's own document corpus, with citations
resolved against source bytes and mandatory human approval before export.
Arabic and English throughout.

| | |
|---|---|
| Frontend | Next.js 16 (App Router), Tailwind v4, shadcn/ui, next-intl |
| Backend | FastAPI, Celery |
| AI | Claude Opus 5 (generation) / Haiku 4.5 (extraction), LangChain 1.x |
| Data | PostgreSQL + RLS (metadata), Qdrant (vectors), Redis, S3 |
| Parsing | Unstructured.io — Arabic OCR + table structure inference |

**Read [`ARCHITECTURE.md`](./ARCHITECTURE.md) first.** It carries the folder
structure, the data flow from upload to approved answer, and the four
fail-closed gates that implement the zero-hallucination constraint.

## Local setup

```bash
cp .env.example .env                 # fill ANTHROPIC_API_KEY + VOYAGE_API_KEY

# backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt  # needs system tesseract-ocr, ara traineddata, poppler-utils

# frontend
cd ../frontend && npm install
```

## Status

Task 1 (structure, dependencies, architecture) complete. No application logic yet.
