# Open Questions, Unknowns & Doc Inconsistencies

**Rule:** never silently resolve anything here. Ask the user, or use a clearly marked
placeholder (`TODO(open-question): ...`). When the user answers, update this file (move
the item to "Resolved" with the date and answer).

## Unknown — not in any document
| # | Topic | Notes |
| :-- | :-- | :-- |
| U1 | Actual program catalogue of IIT / ISB / ISA | Names, durations, contents, débouchés, passerelles. Only known: IIT has Licence, Prépa, Ingénierie. Must come from the official corpus (Sprint S0). |
| U2 | Eligibility rules (Bac / Licence / Prépa) | v3 says YAML rules maintained by Admin; actual thresholds unknown. |
| U3 | Fees, deadlines, campaign dates | Must be served from a DB table, never generated. Values unknown. |
| U4 | `score_compatibilite()` weights & features | Only constraint: academic + stated-interest features only (bias). |
| U5 | Exact LLM, VLM, embedding model, Whisper size | Only "open-source 7–8B instruct + QLoRA" and "hosted API fallback" are decided. |
| U6 | Hosted fallback provider | Not specified. |
| U7 | Repo layout, monorepo vs multi-repo, package managers, Python/Node versions | Not specified. Repo currently contains only `Docs/` and `README.md` (as of 2026-10-23). |
| U8 | Who holds which team role | Team matrix has roles but no names. |
| U9 | QCM question bank content | Admin-approved bank; content unknown. |
| U10 | Message templates (emails/SMS) and languages used per channel | RH-approved; content unknown. |
| U11 | Deployment target (local Docker, cloud, GPU availability) | Not specified. |
| U12 | Retention durations | v3 table is a *proposal* to validate with NAU administration. |

## Scope to confirm
| # | Topic | Notes |
| :-- | :-- | :-- |
| S1 | **ISA in scope?** | Official spec covers IIT + ISB only. ISA comes from the VisioroAI whiteboard and v2; v3 keeps it as an "announced extension" pending confirmation with M. Ben Abdallah. |
| S2 | Actor model for the diagram | v3 merges Resp. académique + Resp. entretien into "NAU Responsable"; may need specialisation if the jury wants the spec's 6 actors. |
| S3 | ATLAS & TTS | Optional / stretch only. |

## Inconsistencies spotted in the docs
| # | Where | Issue |
| :-- | :-- | :-- |
| I1 | SC V3 §3 stack table | Says "reduces ops for a **7-person** team" — team is **5** members everywhere else. Treat as typo. |
| I2 | SC V3 §1 vs §12 | §1 says "3 primary + **3** secondary" actors; §12 and §2 table give 3 primary + **2** secondary (Direction, Admin) + system actors. Use §2 table. |
| I3 | Project name | Spec title = "SmartCampus AI"; spec §1 body says "CampusGuide AI". Use **SmartCampus AI**. |
| I4 | Step 2 owner | Pipeline table lists step 2 owner as VISION 1.2 (Librarian, RAG) while profile building is described under 1.3 Gatekeeper. Step 2 = Q&A + profile learning; both sub-agents involved. |
| I5 | Spec "Whisper (STT / TTS)" | Whisper is STT only; TTS needs another model (optional). |
| I6 | Appointment state machine | Only available as a graphical diagram (SC V3 p. 7); the textual transitions in data_model.md are an interpretation. |

## Resolved
_(none yet)_
