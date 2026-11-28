# SmartCampus AI — Frontend Application

Next.js 16 (App Router) + React 19 + TypeScript + Tailwind CSS v4 frontend for **SmartCampus AI**, the multilingual (French, Tunisian Derja, Arabizi, Modern Standard Arabic) and multimodal (text, voice, VLM transcript OCR) orientation and admission platform for **IIT** and **ISB** (plus **ISA** extension within the NAU Sfax campus).

## Workspaces and Routes

1. **`/` — Visitor to Candidate Workspace (`src/app/page.tsx`)**
   - **Visitor stage (no account required):** Initializes a 7-day signed guest session (`POST /api/v1/auth/guest-session`) for prospective students, graduates, or parents/relatives to ask questions, verify eligibility, and receive cited orientation recommendations via the **VISION** supervisor (`1.1_Listener`, `1.2_Librarian`, `1.3_Gatekeeper`, `1.5_Advisor`).
   - **Voice note review flow:** Captures browser audio via `MediaRecorder`, transcribes locally via Whisper STT (`POST /api/v1/voice-notes/transcribe`), and presents an editable review card before dispatching the confirmed transcript to VISION (`POST /api/v1/voice-notes/process`).
   - **Candidate transition ("Give or Keep"):** Email OTP verification (`POST /api/v1/auth/otp/send` and `POST /api/v1/auth/otp/verify`) automatically merges the anonymous Visitor session into the authenticated Candidate dossier, records explicit INPDP (Law 2004-63) consent (`POST /api/v1/auth/consent`), and unlocks official transcript VLM extraction (`POST /api/v1/documents/extract-and-sync-transcript`, `1.4_Reader`).

2. **`/responsable` — NAU Responsable Portal (`src/app/responsable/page.tsx`)**
   - **Human-in-the-Loop (HITL) admission decisions:** Reviews candidate dossiers, 1-page synthesis briefs (`1.6_Registrar`), VLM transcript confidence flags, and interview slots, and signs off final `ADMITTED`, `WAITLIST`, or `REJECTED` decisions. No AI agent can decide admissions.
   - **Catalog and RAG Corpus DB management:** Allows school-scoped Responsables (`IIT`, `ISB`, `ISA`) to update official program metadata (`GET` / `PATCH /api/v1/catalog/programs`) and upload versioned official RAG documents (`GET` / `POST /api/v1/knowledge/documents`) indexed with `bge-m3` (`vector(1024)` in PostgreSQL 16 + `pgvector`).

## Environment Configuration

Configure the backend API base URL via environment variable (defaults to `http://localhost:8000/api/v1` when unset):

```bash
NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
```

## Development and Verification Commands

### Via Docker Compose (from repository root)

```bash
# Start full stack (PostgreSQL 16 + pgvector, Redis, FastAPI backend, Next.js frontend)
docker compose up -d --build frontend

# Run ESLint and strict TypeScript type-checking inside the container
docker compose exec -T frontend sh -c "npm run lint && npx tsc --noEmit"
```

### Local Node.js Development (inside `frontend/`)

```bash
npm install
npm run dev
npm run lint
npx tsc --noEmit
npm run build
```

The application is served at `http://localhost:3000` (`/` and `/responsable`).
