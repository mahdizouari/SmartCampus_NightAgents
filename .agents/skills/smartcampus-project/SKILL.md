---
name: smartcampus-project
description: >-
  MANDATORY project context for the SmartCampus AI repository (Groupe 3 "Night Agents",
  Projet N°3 IA Générative, IIT/NAU 2026-2027). Use this skill BEFORE any task in this
  repository: writing code, designing architecture, creating diagrams (use-case, class,
  sequence), writing reports or slides, answering questions about the project, or
  planning sprints. It contains the official requirements, the v3 architecture decisions
  (VISION / JARVIS / HITL), the data model, the evaluation plan, and the rules that
  prevent hallucinating features, numbers or program facts.
---

# SmartCampus AI — Project Skill

Agentic, multilingual (French · Tunisian Derja · Arabizi), multimodal (text / voice)
orientation & admission platform for **IIT** and **ISB** (+ **ISA** as an announced
extension), part of the NAU ecosystem (Université Nord-Américaine Privée, Sfax).

> **Defense pitch:** "VISION talks, JARVIS follows up, humans decide: agents handle the
> volume, rules bound what they may do, and every recommendation is cited, measured and
> signed off by a person."

## 0. Pre-work checklist (do this every time)

1. Read this file fully.
2. Open the reference(s) relevant to the task (see §6). For any precise fact, check the
   verbatim source text in [`references/source_text/`](./references/source_text/).
3. Check [open_questions.md](./references/open_questions.md) — never silently "decide"
   something listed there; ask the user.
4. Inspect the **current** repository state (`list_dir`, `git status`) — the docs
   describe the target, not what is already implemented.
5. If the request conflicts with a recorded v3 decision, point it out and ask before
   deviating.

## 1. Source hierarchy (what wins when docs disagree)

| Priority | Source | Location |
| :-- | :-- | :-- |
| 1 | Explicit user instruction in the current conversation | — |
| 2 | Official spec — *Projet N°3* (teacher, M. Taoufik Ben Abdallah) | `Docs/Projet_IAG_p3_26_27.pdf` |
| 3 | Team master architecture — *SC V3* (reviewed edition) | `Docs/SC V3.pdf` |
| 4 | One-page pipeline overview | `Docs/SmartCampus_AI_Pipeline.pdf` |
| 5 | VisioroAI whiteboard photo (handwritten, low detail) | `Docs/1.jpeg` |

v3 explicitly **supersedes v2**. Never reintroduce v2 behaviours listed in the
correction log (see [architecture_v3.md §12](./references/architecture_v3.md)).

## 2. Anti-hallucination rules (non-negotiable)

- **Never invent program facts**: fees, deadlines, admission thresholds, prerequisites,
  program names, durations, course contents, debouchés of IIT/ISB/ISA. None of these are
  in the docs. Use clearly marked placeholders (`TODO(corpus): ...`) or ask the user.
  At runtime the product itself must serve such facts from RAG / DB tables only.
- **Never invent metrics or results.** v3 forbids unsourced claims ("3× conversion",
  "−92 % memory", "5,000+ users", "zero hallucination", "90 % of volume"). Targets in
  the evaluation plan are *targets to measure*, not achieved results.
- **Never add an agent tool that decides admission.** Only the NAU Responsable admits /
  waitlists / rejects (human-in-the-loop).
- **Don't call deterministic code an "AI agent".** Label every unit LLM / VLM / rules /
  code as in v3 §5.
- **Don't assume the stack beyond what's decided** (see §4). If a library/version is
  not specified, say so and propose, don't present it as decided.
- **Cite where a claim comes from** (doc + section) when writing reports, slides or
  design docs.
- If information is missing or ambiguous → **ask**, don't guess.

## 3. Core concepts at a glance

**Actors** — Primary: Candidat/Student, NAU Responsable (one per school; merges
"Resp. académique IIT/ISB" + "Resp. entretien"), Human RH (exceptions). Secondary:
Direction, Platform Admin. System: JARVIS (initiates use cases), external systems.

**Agents** — 2 orchestrators + 9 sub-agents (+1 optional):

| Orchestrator | Sub-agents | Type |
| :-- | :-- | :-- |
| **VISION** (front-office, LangGraph supervisor, steps 1–7) | 1.1 Listener (STT + dialect NLU) · 1.2 Librarian (RAG Q&A, cited) · 1.3 Gatekeeper (profile + eligibility) · 1.4 Reader (relevé VLM) · 1.5 Advisor (orientation score) · 1.6 Registrar (form, QCM, brief) | LLM · LLM · LLM+rules · VLM · code+LLM · LLM |
| **JARVIS** (back-office, event + timer driven, stateless, step 8) | 2.1 Scheduler · 2.2 Messenger · 2.3 Fixer | code · LLM · rules+LLM |
| **ATLAS** (optional / stretch) | Direction NL → read-only SQL → chart | LLM |

Plus a **cross-cutting guardrail node**: citation for factual claims, no admission
promise, no invented fees, reply language matches the student.
VISION and JARVIS **never call each other**: VISION writes a `pre-inscription submitted`
event (outbox); JARVIS reacts.

**9-step pipeline** — ① Discover (no account): 1 Talk · 2 Ask & build profile ·
3 Check eligibility · 4 Recommend 2–3 tracks → **login here** → ② Enrol: 5 Log in
(anonymous chat merged) · 6 Pre-inscription (relevé upload → OCR pre-fill, consent,
dossier routed to school) · 7 Short QCM (5–10 q.) + 1-page brief → ③ Follow up:
8a Book · 8b Remind & reschedule · 8c Problem → Human RH → ④ Decide: 9 Interview &
human decision → student notified.

**Candidate status:** `ANONYMOUS → PROFILING → ELIGIBLE | INELIGIBLE → ORIENTED →
AUTHENTICATED → PREINSCRIBED → QCM_DONE → SCHEDULED → INTERVIEWED → ADMITTED |
WAITLIST | REJECTED`; side states `DORMANT, ESCALATED, WITHDRAWN`.

## 4. Decided stack (v3)

Next.js/React · FastAPI · PostgreSQL 16 + pgvector (one server: `core` + `iit` / `isb` /
`isa` schemas + `knowledge`, RLS + separate roles) · Redis (hot sessions, TTL 7 d;
also Celery broker) · Celery (timers) · MinIO/S3 (relevés, cold archive) · Whisper STT
(TTS optional) · LangGraph + Postgres checkpointer (`interrupt()` for HITL) ·
open-source 7–8B instruct LLM + QLoRA (hosted API fallback for demo) · VLM for relevé ·
SMTP/SMS + calendar API · Langfuse/LangSmith · Google OAuth / email OTP · k6/Locust.

## 5. Key design rules (must hold in any code or design)

1. **Auth rule "give or keep"**: chatting/orientation is anonymous; auth only at the
   first of: upload a document · save/email shortlist · start pre-inscription / book.
2. **Orientation uses declared grades**; the official relevé is uploaded **after auth**.
3. **3-tier memory**: hot Redis (last 6–8 turns) · warm Postgres profile + rolling
   summary (≤ 300 tokens, every 6 turns) · cold gzip JSONL in MinIO.
4. **One owner per case** (`JARVIS` or `RH:<user>`); RH takeover stops all automation.
5. **JARVIS bounds**: propose, book, remind, reschedule (max 2), release slots. Never
   cancel a dossier, promise admission, grant exceptions, decide.
6. **Template-bound messages** approved by RH; LLM only personalises.
7. **Idempotency key** = `appointment_id + message_type`; no double-booking
   (unique `(responsable_id, start_time)` + `SELECT … FOR UPDATE`).
8. **Every state-changing agent action is logged** (who / what / why). Humans never talk
   to sub-agents.
9. RAG Q&A (Librarian) is available **at every step**, not only during scoring.

## 6. Reference index (open on demand)

| File | Read when |
| :-- | :-- |
| [spec_summary.md](./references/spec_summary.md) | Requirements, actors' needs, objectives, presentation format |
| [architecture_v3.md](./references/architecture_v3.md) | Agents, pipeline steps, JARVIS, escalation, risks, correction log |
| [data_model.md](./references/data_model.md) | DB schemas, tables, state machines, retention, memory tiers |
| [evaluation_roadmap.md](./references/evaluation_roadmap.md) | Metrics/test sets, sprints, team roles & ownership |
| [open_questions.md](./references/open_questions.md) | Unknowns & doc inconsistencies — ask before deciding |
| [source_text/](./references/source_text/) | Verbatim text of the PDFs — ground truth for exact wording |

## 7. Validation before finishing a task

- Does the output respect §2 and §5? Any invented fact/number → remove or mark TODO.
- Does it match the candidate status / appointment state names exactly?
- Is each component correctly labelled (LLM / VLM / rules / code)?
- Does it map back to a spec step (traceability table in architecture_v3.md §4)?
