# Evaluation Plan, Roadmap & Team

Source: `Docs/SC V3.pdf` §10–§11. Verbatim:
[02_architecture_SC_V3.txt](./source_text/02_architecture_SC_V3.txt).

> [!IMPORTANT]
> Every number below is a **target to measure**, always against a baseline (base model
> vs fine-tuned, no-RAG vs RAG). Never report a target as an achieved result. Test sets
> are **team-built** and do not exist yet unless found in the repo.

## Evaluation plan

| Component | Test set | Metric | Target |
| :-- | :-- | :-- | :-- |
| Speech (Whisper) | 100+ Tunisian voice notes, varied speakers | WER | Report baseline → adapted; aim −20 % relative |
| Dialect NLU | 300 utterances FR / Derja / Arabizi, labelled intents | Accuracy, macro-F1, per-variety | ≥ 85 % accuracy; QLoRA > base |
| RAG retrieval | 100 questions with gold passages | Recall@5, MRR | Recall@5 ≥ 0.90 |
| RAG answers | Same + 20 out-of-corpus traps | Faithfulness, answer relevancy (RAGAS), correct refusal | Faithfulness ≥ 0.85 · refusal ≥ 95 % |
| Relevé OCR | 30 anonymised transcripts | Field-level exact match; % flagged correctly | ≥ 95 % fields |
| Orientation | 30 profiles judged by 2 advisors | Advisor's choice in top-3; agreement | ≥ 80 % |
| QCM & brief | 20 generated QCMs | Expert rating 1–5; answer-key errors | ≥ 4/5 · 0 key errors |
| JARVIS | Simulated campaign: 200 fake candidates, random cancels / no-shows | % closed without human; double-bookings; on-time reminders | 0 double-booking · 100 % on time |
| End-to-end | 15 scripted personas | Task success, turns to shortlist, p95 latency, cost / candidate | p95 < 4 s (text) |
| Load | k6 / Locust | Concurrent sessions sustained at p95 target | Measure and report |

Banned unsourced claims: "3× conversion", "−92 % memory", "5,000+ concurrent",
"zero-hallucination", "90 % of volume".

## Roadmap (6 sprints × 2 weeks)

| Sprint | Deliverable | Demo |
| :-- | :-- | :-- |
| S0 · W1–2 | Corpus collection (IIT/ISB/ISA brochures, rules, FAQ), schemas, test-set protocol, repo + CI | Corpus indexed, DB migrated |
| S1 · W3–4 | Text chat + RAG Q&A with citations, guardrails v1 | "chnowa el farq bin Prépa w Licence?" → cited answer |
| S2 · W5–6 | Voice + Arabizi normaliser, QLoRA baseline; profiling, eligibility, orientation | Voice note → shortlist with score breakdown |
| S3 · W7–8 | Auth + merge, pre-inscription form, relevé OCR, routing to school schemas, QCM + brief | Full Phase A end-to-end |
| S4 · W9–10 | JARVIS (slots, reminders, replies, waitlist), RH board, Responsable portal | Cancel → reschedule → escalation live |
| S5 · W11–12 | Evaluation campaign, load test, Direction dashboard (+ ATLAS if time), slides & demo script | Final 20-min presentation |

## Team matrix (5 members)

Members: Mehdi Zouari (Lead), Moataz Triki, Yassine Guermazi, Melek Rekik, Youssef
Trabelsi. **The docs do not assign names to roles** — ask the user before attributing.

| # | Role | Steps | Key deliverables |
| :-- | :-- | :-- | :-- |
| 1 | PO / Scrum Master + Evaluation | 3, 7, 9 | Eligibility & orientation rules with schools, QCM bank, brief template, test sets & metrics, report & slides |
| 2 | AI & NLP Architect | 1, 6 | Whisper pipeline, Arabizi normaliser, dialect dataset, QLoRA fine-tuning, relevé VLM extractor |
| 3 | Data & RAG Engineer | 2, 4, 6 | Corpus ingestion, hybrid retrieval, citations, `score_compatibilite()`, core + IIT/ISB/ISA schemas |
| 4 | Backend & Agents Engineer | 1–8 | FastAPI, auth, VISION LangGraph supervisor, memory tiers, guardrails, JARVIS workers, calendar/email |
| 5 | Frontend & UI Developer | 1, 6, 8, 9 | Chat UI (voice, OCR card, QCM), RH board, Responsable portal, Direction dashboard |

Pairing: 2↔3 on RAG + dialect model; 4↔5 on dashboards and JARVIS.
**Scope guard:** ATLAS and TTS stay optional; fine-tune only **one** model.

## Demo script (20-min presentation)
Spec's "exemple complet de besoin": lycéen sends Tunisian voice note, likes maths &
informatique, hesitates Prépa vs Licence → shortlist → pre-inscription → QCM → slot
booked → cancels by message → JARVIS reschedules. Target: < 5 minutes.
