# Running it locally

There is no hosted URL yet — nothing is deployed. Everything below runs on your
own machine.

Pick the tier that matches what you want to see. Tier 1 needs nothing but Node
and takes about two minutes.

---

## Tier 1 — the interface only (~2 min)

Shows every screen with placeholder data: sidebar, Knowledge Base with
drag-and-drop, and the tender workspace. **Requires only Node 20.9+.**

```bash
git clone https://github.com/fahd5897-bot/signup.git
cd signup/frontend
git checkout claude/genai-rfp-saas-architecture-iguf8d

npm install
npm run dev
```

Open **http://localhost:3000** — it redirects to `/en/knowledge-base`.

Worth visiting:

| URL | What it shows |
|---|---|
| `/en/knowledge-base` | Upload dropzone + document table with the text-quality column |
| `/ar/knowledge-base` | The same screen fully mirrored in Arabic |
| `/en/tenders` | Tender list |
| `/en/tenders/3f8a1c92-5b47-4e21-9d63-8a2f7c104e55` | The split-view workspace |

**What will not work in Tier 1:** uploading a file (the request fails — no
backend), and "Auto-fill Questionnaire" (same). You will see the loading
skeleton and then an error. That is expected here.

---

## Tier 2 — with the backend (~10 min)

Lets you exercise the generation flow. Needs Python 3.12, Docker, and an
Anthropic API key.

```bash
# 1. datastores
docker run -d -p 6333:6333 qdrant/qdrant
docker run -d -p 6379:6379 redis:7

# 2. backend
cd signup/backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# system dependencies for Arabic OCR (macOS shown; apt on Linux)
brew install tesseract tesseract-lang poppler

cp ../.env.example ../.env      # then fill in ANTHROPIC_API_KEY and VOYAGE_API_KEY
uvicorn app.main:app --reload --port 8000

# 3. frontend, in a second terminal
cd signup/frontend
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1 npm run dev
```

**Known blockers in Tier 2**, all documented in the phase-6 review:

- **There is no login.** `app/security/` is empty and no `/auth/login` endpoint
  exists, so nothing issues a token. Every request returns 401 until you mint a
  token by hand with the `JWT_SECRET` from your `.env`.
- **There are no database migrations.** `alembic.ini` is missing and
  `migrations/versions/` is empty, so no tables exist and the Row-Level Security
  policies have never been applied. Anything that writes to PostgreSQL fails.
- **Nothing persists.** Generated answers are returned to the browser and lost
  on refresh; the service layer that would save them is not built.

So Tier 2 demonstrates *upload → parse → index → generate → cite*, and nothing
past that.

---

## What is actually finished

| Working | Not built yet |
|---|---|
| Arabic/English parsing, chunking, indexing | Authentication (no login at all) |
| Grounded generation with citations | Database migrations + RLS policies |
| The four anti-hallucination gates | Persistence (service layer) |
| Full UI in both languages | Review / approve / export |
| CORS and the API integration layer | Requirement extraction from the tender |

**No user can log in and complete a tender today.** See the phase-6 summary for
the full gap list and the suggested build order.
