# Data Model, State Machines & Memory

Source: `Docs/SC V3.pdf` §4, §6, §7, §9. Verbatim:
[02_architecture_SC_V3.txt](./source_text/02_architecture_SC_V3.txt).

## Database layout (one PostgreSQL 16 server)

| Schema | Contents |
| :-- | :-- |
| `core` | accounts · candidates · status · consent · appointments · slots · events · outbox · audit |
| `iit` | dossiers · QCM · briefs · decisions |
| `isb` | dossiers · QCM · briefs · decisions |
| `isa` | dossiers · QCM · briefs · decisions (ISA = extension, see open_questions) |
| `knowledge` | pgvector chunks · metadata school / program / source / date |

- Separate DB roles per school + row-level security: school staff cannot read another
  school's dossiers.
- Routing at submission (step 6): dossier written to `core` + school schema in **one
  transaction**. Router sits behind an outbox event so physical separation later only
  changes the worker, not the agents.

## Minimum tables (exact names from v3, for the class diagram)

```text
candidate(id, email, status, school_target, owner)
candidate_profile(candidate_id, facts jsonb, summary text, updated_at)
consent(candidate_id, purpose, version, given_at)
document(id, candidate_id, type, uri, ocr_json, min_confidence)
shortlist(candidate_id, program_id, score, breakdown jsonb, citations jsonb)
slot(id, responsable_id, start_time, status, held_until)
appointment(id, candidate_id, slot_id, state, reschedules)
appointment_event(id, appointment_id, type, actor, reason, at)
rh_ticket(id, candidate_id, reason, summary, owner, status)
outbox(id, event_type, payload, processed_at)
```

Constraints: unique `(responsable_id, start_time)` on slot + `SELECT … FOR UPDATE` →
no double-booking. `slot.status ∈ {FREE, HELD, BOOKED}`.
These are the *minimum*; tables for programs, QCM, briefs, decisions, accounts, audit,
fees/deadlines (served from DB, never generated) are implied but **not specified** —
ask/propose before inventing columns.

## Candidate status (single column drives every dashboard)

```text
ANONYMOUS → PROFILING → ELIGIBLE | INELIGIBLE → ORIENTED → AUTHENTICATED →
PREINSCRIBED → QCM_DONE → SCHEDULED → INTERVIEWED → ADMITTED | WAITLIST | REJECTED
side states: DORMANT, ESCALATED, WITHDRAWN
```

## Appointment state machine

```text
AWAITING_SLOT → INVITED → BOOKED → REMINDED → ATTENDED → (Step 9 decision)
INVITED --no reply 72 h--> ESCALATED (owner = Human RH)
BOOKED/REMINDED --reschedule ≤ 2--> (back to booking)
--3rd change--> ESCALATED
--cancel--> CANCELLED (slot released)
REMINDED --absent--> NO_SHOW → human call (escalation)
ESCALATED --RH hands back--> back to JARVIS flow
ESCALATED --closed--> WITHDRAWN
```
(Diagram in the PDF is graphical; transitions above are the textual reading of it —
verify against `Docs/SC V3.pdf` p. 7 when drawing the official diagram.)

## Case ownership
`candidate.owner` / `rh_ticket.owner` ∈ `{JARVIS, RH:<user>}`. Exactly one owner.

## 3-tier conversation memory

| Tier | Store | Content | Lifetime |
| :-- | :-- | :-- | :-- |
| Hot | Redis | Current graph state + last 6–8 turns | TTL 7 d (anonymous), refreshed on activity |
| Warm | PostgreSQL | `candidate_profile` typed facts (level, averages, interests, choices) + rolling summary ≤ 300 tokens, updated every 6 turns or on topic change; checkpointer keeps only latest checkpoint per thread | Persistent |
| Cold | MinIO / S3 | Raw transcript gzip JSONL for audit & evaluation | Anonymous/ineligible purged at TTL; others deleted/anonymised after campaign |

On authentication: anonymous profile + summary merged into the account.

## Retention policy (proposal — to validate with the administration)

| Data | Where | Kept for |
| :-- | :-- | :-- |
| Anonymous / ineligible sessions | Redis | 7 days, then purged |
| Raw chat transcripts | MinIO (gzip) | Campaign close + 6 months, then deleted or anonymised for evaluation |
| Relevé images | MinIO (encrypted) | Until final decision + period required by NAU |
| Profile, dossier, QCM, decision | PostgreSQL | Admitted: transferred to student record · others: per NAU policy |
| Appointment events, audit log | PostgreSQL | Aggregated to stats after campaign; audit per policy |
