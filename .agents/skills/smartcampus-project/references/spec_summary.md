# Official Spec Summary — Projet N°3 (IA Générative 2026-2027)

Source: `Docs/Projet_IAG_p3_26_27.pdf` (4 pages, French). Verbatim text:
[01_official_spec_Projet_N3.txt](./source_text/01_official_spec_Projet_N3.txt).

- **Title:** SmartCampus AI – Plateforme agentique d'orientation universitaire
  (the spec also uses the name "CampusGuide AI" once, in §1).
- **Institution:** Université Nord-Américaine Privée (Sfax) — Institut International de
  Technologie (IIT), Département Informatique.
- **Course:** IA générative · 3ème année Génie Informatique · Semestre 1.
- **Teacher:** Taoufik Ben Abdallah · in collaboration with VisioroAI.
- **Scope in spec:** IIT parcours **Licence, Préparatoire, Ingénierie** + **ISB**
  formations. (ISA is NOT in the official spec — team extension.)
- The spec is **indicative**: features may be added or removed. Follow-up via the
  "Project Supervision" app; validated by an end-of-semester presentation.

## Pitch (spec wording, translated)
Conversational agentic system talking spontaneously with future students in French,
Tunisian dialect or Arabizi, by text or audio. Explains IIT/ISB programs, progressively
builds the candidate profile, proposes a justified orientation, supports
pre-registration, follows up until the registration appointment, with targeted QCM and
human intervention for official decisions.

## Processus métier ciblé (5 steps)

| # | Service | Role | Expected output |
| :-- | :-- | :-- | :-- |
| 1 | Conversation multimodale | Understand candidate in FR/Tunisian/Arabizi, text or audio; keep context | Initial profile & intents |
| 2 | Orientation académique | Adaptive questions; analyse interests, level, preferences, goals; compare paths | Shortlist with justification |
| 3 | Information programmes | Query official IIT/ISB RAG corpus: conditions, contents, débouchés, passerelles, duration, modalities | Reliable answers & recommended path |
| 4 | Préinscription & QCM | Collect data, create pre-registration, generate targeted QCM | Candidate file + personalised QCM |
| 5 | Suivi admission | Plan reminders, propose appointment, send emails, follow until final registration | Appointment ready, file tracked |

## Objectives
- Centralise official info on IIT Licence / Prépa / Ingénierie and ISB programs.
- Natural, spontaneous, contextual conversation in FR / Tunisian / Arabizi, text or audio.
- Progressively build the profile (results, interests, preferences, constraints, career).
- Propose **several** relevant orientations with explanations, advantages, prerequisites,
  compatibility level — **without imposing a choice**.
- Automatically orchestrate orientation → pre-registration → QCM → appointment → follow-up.
- Generate a targeted QCM before the interview (knowledge, motivation, support needs).
- **Adapt at least one model** to the Tunisian/Arabizi context and **objectively
  evaluate** understanding, intents and answer quality.

## Actors (spec lists 6)
1. **Candidat / futur étudiant** — chat (text/audio, 3 language varieties), ask freely,
   get personalised recommendations, compare, ask about débouchés/conditions,
   pre-register, fill info, take QCM, view appointment, receive confirmations/reminders.
2. **Responsable académique IIT** — view IIT-oriented candidates, profile &
   recommendations, check coherence, view QCM results, confirm/adjust recommendation,
   manage IIT program info used by system, view interest/orientation stats.
3. **Responsable académique ISB** — same, for ISB.
4. **Responsable entretien** — candidates ready for interview, conversation summary,
   profile, recommended program, generated QCM + answers, points to explore, define/
   validate appointment, send invitation, record result & observations, request more
   info or forward the file.
5. **Direction** — global dashboard: conversations, orientations, pre-registrations,
   interviews, enrolments; most demanded programs; IIT/ISB comparison; conversion
   visitor → pre-registration → interview → enrolment; agent KPIs; pending/abandoned
   files; decision-support reports.
6. **Administrateur de la plateforme** — accounts/roles/rights; programs, campaigns,
   workflow params; RAG documents; orientation rules; QCM/email/notification templates;
   supervise agents & tools; logs & errors; AI model / transcription / integration
   settings; traceability & maintenance.

## AI feature examples (spec)
Language detection + code-switching · voice transcription (optional audio reply) ·
conversational memory (level, interests, preferences, asked questions, options) ·
RAG with citation of program/rule/source · orientation profile & multi-criteria
comparison · dynamic next-action planning (ask, explain, compare, start pre-reg,
request human validation) · adaptive QCM + summary for interviewer · personalised
confirmation/reminder/appointment emails triggered only per authorised rules.

## Proposed stack (spec)
Frontend Next.js/React · Backend NestJS/FastAPI · PostgreSQL · Whisper (STT/TTS) ·
LangGraph / Python orchestration · open-source LLM + LoRA/QLoRA · RAG pgvector/FAISS/
Chroma · Email API/SMTP + calendar. (Team chose FastAPI + pgvector — see SKILL.md §4.)

## "Exemple complet de besoin" (use as demo script)
1. A lycéen sends a Tunisian voice note: likes maths & informatique, hesitates Prépa vs
   Licence → transcribe, detect intent, memorise, adaptive questions.
2. Targets software engineering, wants a practical, progressive path → RAG, compare,
   justified shortlist of 2–3.
3. Picks a path, wants to be contacted → collect missing data, **record consent**,
   create pre-registration.
4. Path + school profile known → adaptive QCM, grade, summarise strengths/difficulties/
   motivation → score + synthesis for interview.
5. File ready, no interview → propose an authorised slot, send invite, record
   confirmation.
6. Candidate inactive → personalised reminder, then escalate to an advisor per rules.

## Presentation (20 min)
1. Contexte & problématique · 2. Solution proposée (agentic architecture, global
process, **roadmap**) · 3. Résultats & validation (**metrics**, performance analysis,
discussion) · 4. Conclusion & perspectives.
Avoid code screenshots; prefer diagrams, result tables, pipeline schemas, demos.
