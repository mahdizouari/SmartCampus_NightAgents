#!/usr/bin/env bash
# =============================================================================
# SmartCampus AI — GitHub Labels, Milestones & 30-Issue Sprint 1 Setup Script
# Source: Docs/SmartCampus_Repartition_Sprint1_Mehdi.pdf
# Repo:   mahdizouari/SmartCampus_NightAgents
#
# Prerequisites:
#   1. Install GitHub CLI: https://cli.github.com/
#   2. Authenticate: gh auth login
#   3. (Optional) Fill in GitHub usernames below to auto-assign issues.
#   4. Run: bash scripts/setup_github_backlog.sh
# =============================================================================

set -euo pipefail

REPO="mahdizouari/SmartCampus_NightAgents"

# GitHub usernames for auto-assignment (leave empty to skip --assignee)
GH_MEHDI="${GH_MEHDI:-mahdizouari}"
GH_MOATAZ="${GH_MOATAZ:-moatazz12}"
GH_YASSINE="${GH_YASSINE:-YassineGuermazi}"
GH_MELEK="${GH_MELEK:-melekrekik90}"
GH_YOUSSEF="${GH_YOUSSEF:-yousseftrabelsii}"

if ! command -v gh >/dev/null 2>&1; then
  echo "Error: GitHub CLI (gh) is not installed. Install it from https://cli.github.com/ and run 'gh auth login'."
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "Error: Not authenticated with GitHub CLI. Run 'gh auth login' first."
  exit 1
fi

echo "==> 1. Creating Labels on $REPO..."
create_label() {
  local name="$1" color="$2" desc="$3"
  gh label create "$name" --repo "$REPO" --color "$color" --description "$desc" --force >/dev/null
}

create_label "block: SA-1-architecture"  "1d76db" "Epic SA-1: Architecture & Setup"
create_label "block: SA-11-data-model"    "0e8a16" "Story SA-11: Modèle Relationnel"
create_label "block: SA-15-19-db-api"     "5319e7" "Stories SA-15 & SA-19: Base PostgreSQL & API"
create_label "block: SA-23-auth-sessions" "fbca04" "Epic SA-23: Sessions & Auth"
create_label "block: SA-30-llm-eval"      "006b75" "Epic SA-30: Évaluation & Intégration LLM"
create_label "block: bonus-multimodal"    "e99695" "Epic Bonus: Prétraitement Multimodal & Extraction IA"
create_label "priority: high"             "d93f0b" "Critical path task (Prof High priority)"
create_label "type: qa"                   "bfd4f2" "QA / Testing task"
create_label "status: blocked"            "b60205" "Blocked on upstream dependency"

echo "==> 2. Creating Milestones on $REPO..."
create_milestone() {
  local title="$1" desc="$2"
  local existing
  existing=$(gh api "repos/$REPO/milestones?state=all&per_page=100" --jq ".[] | select(.title == \"$title\") | .number")
  if [ -z "$existing" ]; then
    gh api "repos/$REPO/milestones" -f title="$title" -f description="$desc" >/dev/null
    echo "   Created milestone: $title"
  else
    echo "   Milestone already exists: $title (#$existing)"
  fi
}

M_S1="Sprint 1 — Socle Technique"
M_BONUS="Sprint 1 Bonus — Multimodal & Guardrails"

create_milestone "$M_S1"    "25 core tasks (SA-3 to SA-38): Architecture, PostgreSQL schemas & APIs, Auth/Guest sessions, LLM integration & evaluation."
create_milestone "$M_BONUS" "5 bonus tasks: VLM relevé extraction + DB pre-fill, Whisper STT, Arabizi normalizer, off-topic Guardrail."
create_milestone "Sprint 2 — Orientation & RAG"    "Hybrid pgvector RAG with citations, eligibility rule engine, score_compatibilite()."
create_milestone "Sprint 3 — Préinscription & QCM" "Full pre-inscription form, QCM bank, 1-page interview brief."
create_milestone "Sprint 4 — JARVIS & Portals"     "Appointment scheduling, Celery timers, RH exception board, Responsable portal."
create_milestone "Sprint 5 — Evaluation & Defense" "Full evaluation campaign, k6/Locust load test, Direction dashboard, final demo."

echo "==> 3. Creating 30 Issues (idempotent by title)..."
EXISTING_TITLES=$(gh issue list --repo "$REPO" --state all --limit 500 --json title --jq '.[].title')

create_issue() {
  local title="$1" milestone="$2" labels="$3" assignee="$4" body="$5"
  if echo "$EXISTING_TITLES" | grep -Fxq "$title"; then
    echo "   Skipping existing: $title"
    return
  fi
  local args=(--repo "$REPO" --title "$title" --milestone "$milestone" --body "$body")
  IFS=',' read -ra lbl_arr <<< "$labels"
  for l in "${lbl_arr[@]}"; do
    args+=(--label "$l")
  done
  if [ -n "$assignee" ]; then
    if gh issue create "${args[@]}" --assignee "$assignee" >/dev/null 2>&1; then
      echo "   Created: $title (assigned to @$assignee)"
      return
    else
      echo "   Note: Could not assign @$assignee (pending invite?), creating unassigned: $title"
    fi
  fi
  gh issue create "${args[@]}" >/dev/null
  echo "   Created: $title"
}

# --- MEHDI ZOUARI (Team Leader) ---
create_issue "[SA-4] Définir l'architecture technique globale" "$M_S1" "block: SA-1-architecture" "$GH_MEHDI" \
"**Owner:** Mehdi (Team Leader) | **Epic:** SA-1 Architecture & Setup

### Description
Document the global 5-layer technical architecture (Next.js frontend, FastAPI backend, PostgreSQL + pgvector with \`core\`/\`iit\`/\`isb\`/\`isa\` schemas, Redis hot sessions, MinIO storage, LangGraph VISION/JARVIS separation) and service boundaries.

### Acceptance Criteria
- [ ] Architecture specification documented in \`docs/architecture.md\`
- [ ] Service & data boundaries reviewed by all 5 team members"

create_issue "[SA-12] Concevoir les tables du catalogue (IIT/ISB)" "$M_S1" "block: SA-11-data-model" "$GH_MEHDI" \
"**Owner:** Mehdi (Team Leader) | **Story:** SA-11 Modèle Relationnel | **Depends on:** SA-4

### Description
Design the relational schema for academic programs, tracks, prerequisites, and fees/deadlines tables (strictly DB-driven, never LLM-generated) across IIT and ISB (+ ISA extension).

### Acceptance Criteria
- [ ] ER diagram / SQL schema draft covering programs, tracks, prerequisites, fees/deadlines
- [ ] Validated with Moataz (SA-13) and Yassine (SA-14) before SA-17 migration"

create_issue "[SA-20] Développer les API de gestion du catalogue" "$M_S1" "block: SA-15-19-db-api" "$GH_MEHDI" \
"**Owner:** Mehdi (Team Leader) | **Stories:** SA-15 & SA-19 | **Depends on:** SA-7, SA-12, SA-17

### Description
Implement FastAPI CRUD endpoints (admin/tech-protected) for managing IIT/ISB/ISA programs, tracks, and metadata.

### Acceptance Criteria
- [ ] Create/Update/Delete endpoints for programs & tracks
- [ ] Pydantic request/response validation & RBAC protection"

create_issue "[SA-28] [High] Développer l'authentification (JWT/OAuth)" "$M_S1" "block: SA-23-auth-sessions,priority: high" "$GH_MEHDI" \
"**Owner:** Mehdi (Team Leader) | **Epic:** SA-23 Sessions & Auth | **Priority:** HIGH | **Depends on:** SA-5, SA-7, SA-17

### Description
Implement JWT authentication + Google OAuth / Email OTP following the \"give or keep\" rule (anonymous orientation allowed; auth enforced before document upload, shortlist save, or pre-inscription).

### Acceptance Criteria
- [ ] Register / Login endpoints returning JWT tokens
- [ ] OAuth / OTP flow functional
- [ ] Protected routes reject unauthenticated requests cleanly"

create_issue "[SA-36] Connecter le LLM au Backend" "$M_S1" "block: SA-30-llm-eval" "$GH_MEHDI" \
"**Owner:** Mehdi (Team Leader) | **Epic:** SA-30 Évaluation & Intégration LLM | **Depends on:** SA-7, SA-33

### Description
Create the FastAPI LLM service client connecting the backend to the selected LLM provider with streaming/timeout handling and structured error responses.

### Acceptance Criteria
- [ ] Reusable LLM client service in FastAPI
- [ ] Chat endpoint wired to LLM client with proper error/timeout handling"

create_issue "[Bonus-2] Connecter l'extraction JSON du relevé de notes au profil PostgreSQL" "$M_BONUS" "block: bonus-multimodal" "$GH_MEHDI" \
"**Owner:** Mehdi (Team Leader) | **Epic Bonus:** Story 1 (VLM) | **Depends on:** Bonus-1, SA-17, SA-28

### Description
Build the backend service that takes the VLM-extracted JSON (Bonus-1), validates it with Pydantic, and pre-fills the authenticated candidate's profile in PostgreSQL while flagging low-confidence fields for user confirmation.

### Acceptance Criteria
- [ ] Pydantic validation of VLM JSON payload
- [ ] Authenticated candidate profile updated in PostgreSQL
- [ ] Low-confidence fields flagged for confirmation"

# --- MOATAZ TRIKI ---
create_issue "[SA-3] Définir les acteurs et processus métiers" "$M_S1" "block: SA-1-architecture" "$GH_MOATAZ" \
"**Owner:** Moataz | **Epic:** SA-1 Architecture & Setup

### Description
Formalize the 3 human actors (**Student**, **RH**, **NAU Responsable**) and internal system orchestrators (**VISION**, **JARVIS**), plus the 8-step + parallel sign-up workflow and HITL boundaries (humans decide admissions).

### Acceptance Criteria
- [ ] Actors & business workflows documented
- [ ] HITL rules clearly reflected"

create_issue "[SA-13] Concevoir les tables des utilisateurs et profils" "$M_S1" "block: SA-11-data-model" "$GH_MOATAZ" \
"**Owner:** Moataz | **Story:** SA-11 Modèle Relationnel | **Depends on:** SA-3

### Description
Design \`core\` tables for user accounts, roles (Student/RH/Responsable), candidate profiles, declared grades, and explicit consent records.

### Acceptance Criteria
- [ ] Schema covers users, roles, candidate profiles, declared grades, consent
- [ ] Compatible with SA-12 and SA-14"

create_issue "[SA-17] [High] Créer les tables et les relations SQL" "$M_S1" "block: SA-15-19-db-api,priority: high" "$GH_MOATAZ" \
"**Owner:** Moataz | **Stories:** SA-15 & SA-19 | **Priority:** HIGH | **Depends on:** SA-12, SA-13, SA-14, SA-16

### Description
Implement SQL migrations (e.g. Alembic / SQL scripts) creating all tables, foreign keys, indexes, and logical schemas (\`core\`, \`iit\`, \`isb\`, \`isa\`) in PostgreSQL.

### Acceptance Criteria
- [ ] Migration scripts run cleanly on fresh PostgreSQL instance
- [ ] All foreign keys, constraints, and schemas verified"

create_issue "[SA-25] Développer la gestion des sessions invités" "$M_S1" "block: SA-23-auth-sessions" "$GH_MOATAZ" \
"**Owner:** Moataz | **Epic:** SA-23 Sessions & Auth | **Depends on:** SA-7, SA-26

### Description
Implement signed anonymous session cookies + rate limiting so students can chat and orient without an account (7-day TTL after last activity).

### Acceptance Criteria
- [ ] Anonymous session ID issued and validated via signed cookie
- [ ] Rate limiting applied per session/IP"

create_issue "[SA-34] Évaluer la compréhension du dialecte/Arabizi" "$M_S1" "block: SA-30-llm-eval" "$GH_MOATAZ" \
"**Owner:** Moataz | **Epic:** SA-30 Évaluation & Intégration LLM | **Depends on:** SA-32, SA-36

### Description
Run the evaluation question set (SA-32) on Tunisian Derja and Arabizi inputs, measure intent accuracy / comprehension, and document failure cases.

### Acceptance Criteria
- [ ] Evaluation report comparing French vs Derja vs Arabizi comprehension
- [ ] Failure cases logged for prompt/normalizer tuning"

create_issue "[Bonus-1] Extraire les notes du relevé du Bac en JSON via VLM" "$M_BONUS" "block: bonus-multimodal" "$GH_MOATAZ" \
"**Owner:** Moataz | **Epic Bonus:** Story 1 (VLM)

### Description
Build the VLM extraction module (Qwen-VL / GPT-4o-mini) to parse uploaded Bac transcript images/PDFs into structured JSON (subjects, grades, average, per-field confidence).

### Acceptance Criteria
- [ ] Accepts image/PDF transcript input
- [ ] Outputs structured JSON with grades, average, and confidence scores"

# --- YASSINE GUERMAZI ---
create_issue "[SA-5] Concevoir les API et les règles d'accès" "$M_S1" "block: SA-1-architecture" "$GH_YASSINE" \
"**Owner:** Yassine | **Epic:** SA-1 Architecture & Setup | **Depends on:** SA-3, SA-4

### Description
Define the REST API specification (OpenAPI endpoints, request/response schemas) and RBAC access rules per actor (Anonymous Guest, Authenticated Student, RH, NAU Responsable).

### Acceptance Criteria
- [ ] API contract & RBAC matrix documented
- [ ] Shared with backend and frontend owners"

create_issue "[SA-14] Concevoir les tables de gestion (préinscription/RDV)" "$M_S1" "block: SA-11-data-model" "$GH_YASSINE" \
"**Owner:** Yassine | **Story:** SA-11 Modèle Relationnel | **Depends on:** SA-3

### Description
Design tables for pre-inscription dossiers, QCM results, interview \`slots\` (\`FREE\`/\`HELD\`/\`BOOKED\`), \`appointments\`, and append-only \`appointment_events\`.

### Acceptance Criteria
- [ ] Schema covers dossiers, QCM, slots, appointments, and events
- [ ] Prevents double-booking via unique constraints"

create_issue "[SA-16] Installer et configurer PostgreSQL" "$M_S1" "block: SA-15-19-db-api" "$GH_YASSINE" \
"**Owner:** Yassine | **Stories:** SA-15 & SA-19 | **Depends on:** SA-9

### Description
Configure PostgreSQL 16 (+ pgvector extension) in Docker/local environment with separate roles/schemas (\`core\`, \`iit\`, \`isb\`, \`isa\`, \`knowledge\`) and connection pooling.

### Acceptance Criteria
- [ ] PostgreSQL + pgvector running and accessible from FastAPI
- [ ] Schemas and roles initialized"

create_issue "[SA-26] Mettre en place la mémoire temporaire" "$M_S1" "block: SA-23-auth-sessions" "$GH_YASSINE" \
"**Owner:** Yassine | **Epic:** SA-23 Sessions & Auth | **Depends on:** SA-9

### Description
Set up Redis for hot conversation turns (last 6–8 turns, 7-day TTL) and temporary guest session state.

### Acceptance Criteria
- [ ] Redis service configured and connected to FastAPI
- [ ] Read/write/TTL helpers tested"

create_issue "[SA-32] Préparer les questions pour évaluer les LLMs" "$M_S1" "block: SA-30-llm-eval" "$GH_YASSINE" \
"**Owner:** Yassine | **Epic:** SA-30 Évaluation & Intégration LLM

### Description
Build a structured evaluation dataset covering French, Tunisian Derja, Arabizi, orientation queries, and out-of-corpus trap questions to benchmark candidate LLMs.

### Acceptance Criteria
- [ ] Versioned JSON/CSV test set committed to repo
- [ ] Covers FR, Derja, Arabizi, and out-of-scope traps"

create_issue "[Bonus-3] Intégrer Whisper pour transcrire les messages vocaux (FR / Derja)" "$M_BONUS" "block: bonus-multimodal" "$GH_YASSINE" \
"**Owner:** Yassine | **Epic Bonus:** Story 2 (Whisper/Arabizi/Guardrails) | **Depends on:** SA-7

### Description
Integrate OpenAI Whisper into the backend to transcribe student voice notes (French / Tunisian Derja) into text, returning the transcript for user review/correction before LLM processing.

### Acceptance Criteria
- [ ] Audio upload endpoint transcribes FR/Derja voice notes
- [ ] Returns editable transcript text"

# --- MELEK REKIK ---
create_issue "[SA-7] Initialiser le Backend (FastAPI)" "$M_S1" "block: SA-1-architecture" "$GH_MELEK" \
"**Owner:** Melek | **Epic:** SA-1 Architecture & Setup

### Description
Scaffold the FastAPI project structure (\`app/\`, routers, services, Pydantic config, \`/health\` endpoint, CORS, linting/formatting).

### Acceptance Criteria
- [ ] \`uvicorn\` starts cleanly and \`GET /health\` returns 200 OK
- [ ] Clean modular folder structure"

create_issue "[SA-9] Préparer l'environnement de dév (Docker)" "$M_S1" "block: SA-1-architecture" "$GH_MELEK" \
"**Owner:** Melek | **Epic:** SA-1 Architecture & Setup

### Description
Create \`Dockerfile\`(s) and \`docker-compose.yml\` orchestrating FastAPI, Next.js, PostgreSQL (pgvector), and Redis for one-command local startup.

### Acceptance Criteria
- [ ] \`docker compose up\` starts all core services cleanly
- [ ] \`.env.example\` documented"

create_issue "[SA-18] Alimenter la base avec des données initiales" "$M_S1" "block: SA-15-19-db-api" "$GH_MELEK" \
"**Owner:** Melek | **Stories:** SA-15 & SA-19 | **Depends on:** SA-17

### Description
Write idempotent seed scripts populating initial IIT/ISB/ISA catalog entries, sample test users, and sample interview slots.

### Acceptance Criteria
- [ ] Idempotent seed script runs without duplicating rows
- [ ] Sample data available for API and frontend testing"

create_issue "[SA-22] Vérifier le fonctionnement et sécurité des API (QA)" "$M_S1" "block: SA-23-auth-sessions,type: qa" "$GH_MELEK" \
"**Owner:** Melek | **Epic:** SA-23 Sessions & Auth | **Depends on:** SA-20, SA-21, SA-28

### Description
Write automated API tests (\`pytest\`) verifying all endpoints, RBAC permissions, JWT/guest session boundaries, and input validation.

### Acceptance Criteria
- [ ] \`pytest\` suite covers catalog, auth, and guest endpoints
- [ ] Auth & RBAC edge cases verified"

create_issue "[SA-38] Tester le fonctionnement du dialogue (QA croisé)" "$M_S1" "block: SA-30-llm-eval,type: qa" "$GH_MELEK" \
"**Owner:** Melek | **Epic:** SA-30 Évaluation & Intégration LLM | **Depends on:** SA-29, SA-36, SA-37

### Description
Perform end-to-end cross-testing of multi-turn dialogue (guest → auth merge → LLM memory → guardrail checks) and log bug reports.

### Acceptance Criteria
- [ ] End-to-end dialogue test report documented
- [ ] Bugs filed as separate issues if found"

create_issue "[Bonus-4] Créer le module de normalisation Arabizi → Français structuré" "$M_BONUS" "block: bonus-multimodal" "$GH_MELEK" \
"**Owner:** Melek | **Epic Bonus:** Story 2 (Whisper/Arabizi/Guardrails)

### Description
Build a pre-processing module converting Tunisian Arabizi (e.g., \`3\`, \`7\`, \`9\`, \`5\`, common Derja tokens) into clean normalized text before sending to the LLM.

### Acceptance Criteria
- [ ] Unit tests covering common Tunisian Arabizi patterns
- [ ] Integrated as a pre-processing step before LLM call"

# --- YOUSSEF TRABELSI ---
create_issue "[SA-8] Initialiser le Frontend (Next.js)" "$M_S1" "block: SA-1-architecture" "$GH_YOUSSEF" \
"**Owner:** Youssef | **Epic:** SA-1 Architecture & Setup

### Description
Scaffold the Next.js / React frontend (App Router, Tailwind/UI kit, API client wrapper, base layout for chat + auth modal).

### Acceptance Criteria
- [ ] Next.js dev server runs cleanly
- [ ] Base chat & auth shell ready"

create_issue "[SA-21] Développer les API de consultation du catalogue" "$M_S1" "block: SA-15-19-db-api" "$GH_YOUSSEF" \
"**Owner:** Youssef | **Stories:** SA-15 & SA-19 | **Depends on:** SA-7, SA-17, SA-18

### Description
Implement public read-only FastAPI endpoints to list, filter, and compare IIT/ISB/ISA programs and tracks.

### Acceptance Criteria
- [ ] \`GET /programs\` and \`GET /programs/{id}\` endpoints functional
- [ ] Filtering by school/degree supported"

create_issue "[SA-29] Associer une conversation invité au compte créé" "$M_S1" "block: SA-23-auth-sessions" "$GH_YOUSSEF" \
"**Owner:** Youssef | **Epic:** SA-23 Sessions & Auth | **Depends on:** SA-25, SA-26, SA-28

### Description
Implement the session-merge logic: when a guest signs up or logs in, transfer their Redis conversation history and declared profile to their authenticated PostgreSQL account without losing context.

### Acceptance Criteria
- [ ] Guest history & profile preserved on login/signup
- [ ] Anonymous session cleaned up or linked cleanly"

create_issue "[SA-33] Comparer les performances des modèles LLM" "$M_S1" "block: SA-30-llm-eval" "$GH_YOUSSEF" \
"**Owner:** Youssef | **Epic:** SA-30 Évaluation & Intégration LLM | **Depends on:** SA-32

### Description
Benchmark candidate LLM models using SA-32's test set on latency, French/Derja quality, and instruction following; document the recommended baseline model.

### Acceptance Criteria
- [ ] Comparison table with measured latency & quality scores
- [ ] Recommended baseline model selected for SA-36"

create_issue "[SA-37] Intégrer la mémoire conversationnelle LLM" "$M_S1" "block: SA-30-llm-eval" "$GH_YOUSSEF" \
"**Owner:** Youssef | **Epic:** SA-30 Évaluation & Intégration LLM | **Depends on:** SA-26, SA-36

### Description
Wire Redis hot turns + rolling profile summary into the LLM prompt context so multi-turn conversations remain coherent within token limits.

### Acceptance Criteria
- [ ] Multi-turn context passed from Redis to LLM
- [ ] Context window bounded (last 6–8 turns + summary)"

create_issue "[Bonus-5] Mettre en place un Guardrail IA anti-hors-sujet" "$M_BONUS" "block: bonus-multimodal" "$GH_YOUSSEF" \
"**Owner:** Youssef | **Epic Bonus:** Story 2 (Whisper/Arabizi/Guardrails) | **Depends on:** SA-36

### Description
Implement an input/output guardrail node that blocks off-topic queries (outside IIT/ISB/ISA orientation & admissions), prevents admission promises, and enforces safe refusal responses.

### Acceptance Criteria
- [ ] Off-topic queries refused politely in user's language
- [ ] No admission promises or invented fees/deadlines pass through"

echo "==> Done! All labels, milestones, and 30 issues are synced to $REPO."

