# SmartCampus AI — Agent Rules (always on)

Project: **SmartCampus AI** — agentic orientation & admission platform for IIT / ISB
(+ ISA extension), Groupe 3 "Night Agents", Projet N°3 IA Générative, NAU 2026-2027.

## Mandatory before ANY task in this repo
1. Read `.agents/skills/smartcampus-project/SKILL.md` **completely** and follow its
   pre-work checklist. Open the linked `references/` files relevant to the task.
2. Inspect the current repo state — the docs describe the target system, not what is
   already built.

## Hard rules
- **Do not hallucinate.** Never invent program facts (fees, deadlines, thresholds,
  prerequisites, program names/contents), metrics/results, team role assignments, or
  stack choices not recorded in the skill. Mark gaps as `TODO(...)` or ask.
- **Source of truth order:** user's explicit instruction → `Docs/Projet_IAG_p3_26_27.pdf`
  (official spec) → `Docs/SC V3.pdf` (team architecture v3) →
  `Docs/SmartCampus_AI_Pipeline.pdf` → `Docs/1.jpeg`. v3 supersedes v2.
- **Humans decide admissions.** No agent/tool may admit, waitlist or reject.
- **Keep exact names** from the docs: agents (VISION, JARVIS, ATLAS, sub-agents
  1.1–1.6 / 2.1–2.3), candidate statuses, appointment states, table names.
- If a request contradicts a recorded v3 decision or touches an item in
  `references/open_questions.md`, **stop and ask** before proceeding.
- When the user resolves an open question or changes a decision, update the skill's
  reference files so they stay accurate.
- Never modify files in `Docs/` unless explicitly asked.
