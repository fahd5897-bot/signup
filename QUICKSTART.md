# Running it locally

There is no hosted URL — nothing is deployed. Everything below runs on your own
machine.

Two paths. The first runs the whole stack and is what you want if you intend to
use the product. The second runs the interface alone, in two minutes, if you
only want to look at it.

---

## The whole stack, with Docker (~10 min, mostly image pulls)

Needs Docker, an Anthropic API key, and a Voyage API key (Anthropic ships no
embedding model — see ADR-0001 in `ARCHITECTURE.md`).

```bash
git clone https://github.com/fahd5897-bot/signup.git
cd signup
git checkout claude/genai-rfp-saas-architecture-iguf8d

cp .env.example .env
# Fill in ANTHROPIC_API_KEY and VOYAGE_API_KEY, then generate a real secret:
python -c "import secrets; print(secrets.token_urlsafe(48))"   # -> JWT_SECRET

# Datastores first, so the migration has something to run against.
docker compose up -d db redis qdrant minio minio-init

# Schema, row-level security policies, and the auth lookup function.
docker compose run --rm api alembic upgrade head

docker compose up -d
```

Open **http://localhost:3000**, register an account, and work through:

1. **Knowledge Base** — upload your company's capability documents, certificates,
   and past responses. Ingestion is asynchronous; the table shows live status
   and a text-quality ratio that tells you whether a scanned Arabic PDF actually
   extracted.
2. Upload the **tender** itself with role `tender`, then extract its
   requirements — the compliance matrix appears with every clause outstanding.
3. **Generate** answers. Each one is grounded in your own documents and carries
   citations, or it abstains and says why. Neither outcome is approved.
4. **Review** each answer and approve it. Nothing can be approved without a
   citation unless you explicitly state in writing that you are vouching for it
   yourself.
5. **Export** — DOCX, PDF, or the compliance matrix. Blocked until every
   mandatory requirement is approved, with no override.

The API's own docs are at **http://localhost:8000/docs**.

`JWT_SECRET` has no default in `docker-compose.yml` on purpose: a placeholder
that works locally is a placeholder that reaches production, and the config
validator refuses a short or well-known value at startup.

---

## The interface alone (~2 min)

Every screen, with no backend behind it. Needs only Node 22.

```bash
cd signup/frontend
npm install
npm run dev
```

Open **http://localhost:3000** — it redirects to `/en/knowledge-base`.

| URL | What it shows |
|---|---|
| `/en/knowledge-base` | Upload dropzone and the document table |
| `/ar/knowledge-base` | The same screen, fully mirrored in Arabic |
| `/en/tenders` | Tender list |

Uploading and generating will fail here: there is no API to call. That is
expected on this path.

---

## Running it without Docker

```bash
cd signup/backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# System dependencies. Arabic OCR for scanned tenders, and Pango/Cairo for PDF
# export. macOS shown; on Debian see backend/docker/Dockerfile for the apt list.
brew install tesseract tesseract-lang poppler pango cairo gdk-pixbuf

# PostgreSQL, Redis, Qdrant, and MinIO must be reachable at the URLs in .env.
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Run the API as an unprivileged PostgreSQL role, not as the table owner. The
policies are `FORCE ROW LEVEL SECURITY` so the owner is bound by them too, but
the GRANT separation is real and the first place it should be exercised is not
production. `infra/postgres/init/01-application-role.sh` is the role Compose
creates.

---

## Tests

```bash
cd backend
pytest -q                        # unit tests; integration ones skip themselves

# With a real PostgreSQL, the row-level security and gate tests run too. A
# policy asserted only in Python is a comment, not a security control.
TEST_POSTGRES_DSN=postgresql+asyncpg://postgres@127.0.0.1:5432/rfp pytest -q

ruff check . && ruff format --check .
python scripts/check_filter_usage.py   # every Qdrant filter is tenant-scoped
```

The same three run in CI on every push, against a real PostgreSQL with an
unprivileged role, plus a `downgrade base` round trip to prove the migrations
reverse.

---

## What is built

| Working end to end | Not built |
|---|---|
| Registration, login, roles, tenant isolation via RLS | Billing and plan enforcement |
| Creating tenders, uploading documents, live ingestion status | Go/No-Go analysis (the screen is a placeholder) |
| Arabic/English parsing, chunking, hybrid indexing | Tenant-branded export templates |
| Requirement extraction into a compliance matrix | Assigning a named SME (the escalation control is disabled) |
| Grounded generation with citations, or a stated abstention | SSO |
| Review, edit, approve, reject — in the browser | Kubernetes/Terraform deployment |
| Export to DOCX, PDF, and the compliance matrix | |
| The four anti-hallucination gates, plus the approval gate | |
| Full UI in Arabic and English, right-to-left throughout | |
