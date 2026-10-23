# Architecture v3 — Detailed Reference

Source: `Docs/SC V3.pdf` ("Master Architecture & Pipeline — v3 Reviewed & Enhanced
Edition", 14 pages) + `Docs/SmartCampus_AI_Pipeline.pdf`. Verbatim:
[02_architecture_SC_V3.txt](./source_text/02_architecture_SC_V3.txt),
[03_pipeline_overview.txt](./source_text/03_pipeline_overview.txt).

Team: Groupe 3 "Night Agents" — Mehdi Zouari (Lead), Moataz Triki, Yassine Guermazi,
Melek Rekik, Youssef Trabelsi.

## §1 Recorded decisions

| Question | Decision |
| :-- | :-- |
| When to authenticate? | Not to chat. At first of: upload document · save/email shortlist · start pre-inscription |
| Agent or human RH for follow-up? | Both, with case ownership: JARVIS standard path, Human RH exceptions; one owner at a time |
| Chat history explosion (1,000+ students)? | 3-tier memory: hot Redis · warm structured profile + rolling summary · cold compressed archive |
| RH-agent history after cancellations? | No per-booking transcripts. State-machine rows + append-only events; cancel = status change releasing slot. Agent stateless, rebuilds context from DB |
| 3 DBs physical or logical? | One PostgreSQL server, `core` + 3 school schemas, separate roles + RLS; outbox kept so schools can split later |
| Grades: OCR first or later? | Orientation uses declared grades (anonymous); official relevé uploaded after auth; OCR pre-fills form |

## §2 Actors (use-case diagram input)
- Primary (left): **Student**, **NAU Responsable** (one per school, school attribute;
  can be specialised into Resp. académique + Resp. entretien via generalisation if jury
  wants spec's exact list), **Human RH**.
- Secondary: **Direction**, **Platform Admin**.
- System actor: **JARVIS** (initiates: send reminder, release slot, escalate).
- External: Email/SMS provider, calendar API, identity provider (Google OAuth), STT,
  LLM serving, object storage.
- Use `«include»` for *Authenticate* from *Upload relevé*, *Save shortlist*, *Submit
  pre-inscription*.
- Whiteboard: User/Candidat, Resp NAU ("Admin" crossed out), third role crossed out
  ("Agent RH / Isolée"? — illegible).
- **Golden rule:** humans never talk to sub-agents; they use VISION, JARVIS or their
  dashboard. Every state-changing agent action logged with who / what / why.

## §3 Global architecture (5 layers)
- **Channels:** Student web chat (text, voice note, file upload) · RH exception board
  (queue, ownership) · Responsable portal (slots, brief, decision) · Direction dashboard
  (funnel, KPIs) · Admin console (config, RAG docs, logs).
- **Backend:** FastAPI + Next.js; auth (OTP/OAuth), rate limiting, RBAC per school,
  guardrails (prompt-injection filter, PII masking in logs, "no promise of admission"
  output check), audit log.
- **Agents:** VISION (LangGraph supervisor, 6 sub-agents, steps 1–7) · JARVIS (event
  workers, 3 sub-agents, step 8) · ATLAS optional (read-only SQL).
- **Data:** Redis (hot, TTL 7 d) · PostgreSQL core (accounts, candidates, appointments,
  events, outbox) · schemas iit/isb/isa (dossiers, QCM, decisions, RLS) · pgvector
  (official corpus, metadata school/program) · MinIO (relevés, cold archive).
- **AI & external:** Whisper STT (+opt. TTS) · open LLM + QLoRA Derja/Arabizi adapter ·
  VLM relevé extraction · SMTP/SMS + Calendar API · Langfuse/LangSmith.
- VISION and JARVIS share the data layer but **never call each other**.

## §4 Pipeline — 9 steps, 4 phases

| # | Phase | Step | Owner | AI / tooling | Human touch-point | Fails safely by |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| 1 | ① Discover (no account) | Talk | VISION 1.1 | Whisper, LID, regex Arabizi map, QLoRA LLM | — | Ask to repeat / offer text when STT confidence low |
| 2 | ① | Ask & build profile | VISION 1.2 | pgvector hybrid search (BM25 + vectors), citation check | Admin maintains corpus | "I don't have an official source for that" + contact link |
| 3 | ① | Check eligibility | VISION 1.3 | Rule engine (YAML rules by Admin) | Admin edits rules | Borderline flagged "to confirm by Responsable"; stop early if not eligible + alternatives |
| 4 | ① | Recommend 2–3 tracks | VISION 1.5 | `score_compatibilite()` (weights) + LLM explanation | Responsable may adjust later | Show score breakdown, not just verdict; IIT/ISB/ISA side by side; student picks |
| 5 | ② Enrol | Log in | Backend | OTP / OAuth, session merge | — | Resume link; anonymous data kept 7 days |
| 6 | ② | Pre-inscription | VISION 1.4 / 1.6 | OpenCV, VLM, Pydantic, outbox router | RH on document anomaly | Student confirms highlighted fields; mismatch → RH ticket. Consent recorded. Dossier saved in chosen school DB |
| 7 | ② | Short QCM + brief | VISION 1.6 | Question bank + LLM variants, auto-grader | Admin approves question bank | Generate from vetted bank, not from scratch. 5–10 q., 1-page brief |
| 8 | ③ Follow up | 8a Book · 8b Remind/reschedule · 8c Problem→RH | JARVIS 2.1–2.3 | Celery timers, calendar API, LLM reply understanding | Human RH on exceptions | Escalation matrix; one owner per case. Reminder 24 h before |
| 9 | ④ Decide | Interview & decision | NAU Responsable | Brief + dashboard | Final decision | No decision ever sent without human sign-off; JARVIS sends approved result (email + in-app) |

Traceability to spec: spec 1 → v3 step 1 (+all) · spec 2 → 2,3,4 · spec 3 → 2
(continuous) · spec 4 → 5,6,7 · spec 5 → 8,9.

One-page pipeline "principles": 🔒 Login only when needed · 📚 No invented answers
(every program fact from official docs, shown with source) · 🧑‍💼 Agent + human, one
owner (no reply 72 h, no-show, 3rd cancellation or complaint → Human RH, agent stops) ·
✅ Humans decide.

## §5 Multi-agent hierarchy

| Unit | Role | Type |
| :-- | :-- | :-- |
| VISION | Front-office supervisor (LangGraph); plans next action: ask, explain, compare, start pre-inscription, ask for human validation | LLM supervisor |
| 1.1 Listener | Voice & dialect NLU — STT, Arabizi → clean text, intent | LLM |
| 1.2 Librarian | Program Q&A (RAG) with cited sources | LLM |
| 1.3 Gatekeeper | Profile & eligibility — LLM extracts, rules decide | LLM + rules |
| 1.4 Reader | Relevé extractor — VLM → checked grades + confidence | VLM |
| 1.5 Advisor | Orientation — score → explained 2–3 tracks | code + LLM |
| 1.6 Registrar | Form pre-fill, adaptive QCM, interview brief | LLM |
| JARVIS | Back-office orchestrator, event + timer driven, stateless between triggers | — |
| 2.1 Scheduler | Slots & booking, no double-booking | code |
| 2.2 Messenger | Reminders; understands confirm / move / cancel | LLM |
| 2.3 Fixer | Frees slots, waitlist, opens RH ticket | rules + LLM |
| ATLAS (opt.) | Direction NL questions → SQL over read-only views → chart + comment | LLM |
| Guardrail node | Every VISION/JARVIS output: citation for facts, no admission promise, no invented fees, language matches student | cross-cutting |

Principle: "Agents decide what to do next; rules and code decide what is allowed."

### JARVIS triggers (not a polling LLM)
| Trigger | Example | Mechanism |
| :-- | :-- | :-- |
| Domain event | pre-inscription submitted, slot cancelled, student replied | Outbox row → worker (or Postgres LISTEN/NOTIFY) |
| Timer | reminder T-24 h; no reply after 24/48/72 h | Celery task with ETA, stored with appointment; cancelled on state change |
| Safety sweep | missed event, worker restart | Every 10 min reconcile states vs timers (idempotent) |
Idempotency key = `appointment_id + message_type`.

## §6 Human RH + JARVIS (hybrid with ownership)
Operating rules:
1. `case.owner ∈ {JARVIS, RH:<user>}`; RH takeover stops all automated messages; RH can
   hand back with one click.
2. JARVIS may propose, book, remind, reschedule (**max 2**), release slots. May NOT
   cancel a dossier, promise admission, grant exceptions or decide anything.
3. Template-bound messages: RH approves templates; LLM personalises only (name, date,
   language register).
4. Each RH ticket carries a 5-line LLM summary + timeline.

Escalation matrix (defaults, Admin-configurable):
| Situation | JARVIS does | Escalates to RH when |
| :-- | :-- | :-- |
| Invitation not answered | Reminder 24 h, second at 48 h | No answer at 72 h → `DORMANT`, RH ticket |
| Student cancels / moves | Offers new slots, releases old slot to waitlist | 3rd change request |
| No-show | Marks `NO_SHOW` | Immediately — a human calls |
| Responsable cancels | Apologises, re-offers priority slots | No slot within 7 days |
| Document mismatch / low confidence | Asks student to confirm field | Declared ≠ official after confirmation |
| Complaint, distress, special needs, fees/deadline exception | Acknowledges, no commitment | Immediately |

Appointment state machine: see [data_model.md](./data_model.md).

## §7 Core problems
- **Auth:** "give or keep" rule; anonymous signed session cookie + Redis, rate-limited
  per IP/session; TTL 7 d (v2's 24 h rejected); merge on auth; per-session message
  quota, voice length cap, upload only after auth.
- **Chat history:** sizing estimates (not measured): ~24 MB raw for 1,000 students ×
  60 msgs; ~7,000 tokens/call with full history vs ~1,200 with profile + summary + last
  6 turns (≈6× fewer). "Measure real figures during load test."
- **RH-agent history:** stateless JARVIS; `appointments`, `appointment_events`
  (append-only, ~200 B), `slots` (FREE/HELD/BOOKED). Waitlist auto-fill: offer freed
  slot to next 3 waitlisted with 15-min hold; first confirmation wins.

## §8 Risks & mitigations
| Risk | Impact | Mitigation |
| :-- | :-- | :-- |
| Derja/Arabizi misunderstanding | High | Arabizi normaliser + QLoRA adapter on team dataset; clarification question on low intent confidence |
| Hallucinated program facts | High | Answer only from retrieved chunks; citation required; refusal + contact if no source; **fees/deadlines from a DB table, not generated** |
| Weak Whisper on Tunisian audio | Med | Show transcript for one-tap correction; measure WER; optional fine-tune |
| Prompt injection | Med | Input filter; documents as data only; tools gated by status; no "decide admission" tool |
| Forged relevé | Med | Declared vs official comparison; anomaly → RH; original verified at interview |
| Duplicate candidates | Low | Dedupe on verified email + name + birth date; merge suggestion to RH |
| Peak season load | Med | Async queue + streaming; small model for NLU, larger for explanations; hosted fallback; k6/Locust |
| Email/SMS not delivered | Med | SPF/DKIM, delivery tracking; in-app notification as 2nd channel |
| Personal data compliance | High | Tunisian Loi organique n° 2004-63 (INPDP): explicit consent at step 6, purpose limitation, retention, encryption at rest, PII masked in logs/traces |
| Orientation bias | Med | Score uses only academic + stated-interest features; audit shortlists across groups |
| Agent/human contradiction | Med | Ownership lock + idempotency keys |
| Model/cost drift | Low | Regression eval suite in CI; Langfuse traces with cost/conversation |

## §12 Correction log v2 → v3 (do NOT regress)
1. 3 actors → 3 primary + secondary + system actors. 2. Rebalanced 5-role matrix.
3. Added evaluation section. 4. Added roadmap. 5. Relevé no longer uploaded before auth.
6. RAG available at every step (not only scoring). 7. JARVIS = events + timers + 10-min
sweep (not 60 s polling). 8. Each unit labelled LLM/VLM/rules/code. 9. Unsourced
claims → measured targets. 10. Redis TTL 24 h → 7 d + resume link. 11. 3 physical DBs →
schemas + RLS, outbox as scaling path. 12. Consent table, guardrail node, retention
policy. 13. Case ownership + escalation matrix + state machine. 14. JARVIS sends
human-approved outcome. 15. Direction dashboard + Admin console. 16. Renumbered
pipeline.
