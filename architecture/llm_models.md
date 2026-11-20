# SmartCampus AI — LLM, VLM & Speech Model Allocation

This document separates the AI model assignments across **VISION**, **JARVIS**, and **VLM** workloads in SmartCampus AI (SC V3 / V4), using verified provider model IDs.

> **Governance Reminder (Non-Negotiable):**
> - **HITL Admissions:** No LLM or VLM may admit, waitlist, or reject a candidate (`ADMITTED | WAITLIST | REJECTED`). Final decisions belong exclusively to the human **NAU Responsable**.
> - **No Hallucinations:** Every academic fact (fees, deadlines, admission thresholds, prerequisites, program titles) must come from retrieved official corpus passages or PostgreSQL catalog data.
> - **Empirical Selection:** Model choices below are the active Sprint 1 runtime defaults; final comparative metrics across French, Fusha, Tunisian Derja, Arabizi, and out-of-corpus traps are measured via `architecture/evaluation_benchmark.md` (`SA-32` / `SA-33` / `SA-34`).

---

## 1. Model Allocation by Component

| Component | Agent / Scope | Primary Model | Fallback / Secondary | Provider (`Base URL`) | Why This Model |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **VISION** | Student Orientation & Pre-Inscription (`1.1_Listener` – `1.6_Registrar`) | `openai/gpt-oss-120b` | `openai/gpt-oss-20b` (Groq) or OpenRouter failover | Groq (`https://api.groq.com/openai/v1`) | Strongest multilingual reasoning + tool calling on Groq; stays under `< 4 s` text latency target (`~1.0–2.6 s` in initial smoke test); better Tunisian Derja/Arabizi fidelity and lower acronym hallucination than `qwen/qwen3.8-27b`. |
| **JARVIS** | Admission Scheduling & Follow-Up (`2.1_Scheduler`, `2.2_Communicator`, `2.3_Watchdog`) | `openai/gpt-oss-20b` | `openai/gpt-oss-120b` (escalation for ambiguous replies) | Groq (`https://api.groq.com/openai/v1`) | Fast, low-cost, high-RPM tool calling for short structured tasks: drafting reminders, classifying candidate replies (`confirm \| cancel \| reschedule \| escalate`), and waitlist outreach. Deterministic slot locks & timers stay in PostgreSQL + Celery. |
| **VLM** | Transcript (*Relevé de notes*) OCR & JSON Extraction (`Bonus-1` / `Bonus-2`) | `qwen/qwen3.8-27b` | OpenRouter Vision model (backup) | Groq (`https://api.groq.com/openai/v1`) | Multimodal Vision + structured output on the same Groq API key. Extracts Bac section, subjects, grades, and confidence scores into validated JSON for PostgreSQL persistence (`core.declared_grades` / `core.candidate_profile`), flagging low-confidence fields for human/student verification. |
| **STT** *(Sprint 2)* | Tunisian Voice Notes Transcription (`1.1_Listener`) | `whisper-large-v3` | `whisper-large-v3-turbo` | Groq (`https://api.groq.com/openai/v1`) | High accuracy on Tunisian Arabic / French code-switching; benchmarked against `whisper-large-v3-turbo` for latency vs. WER. |
| **Guardrails** *(Optional)* | Prompt Injection & Content Moderation | `meta-llama/llama-prompt-guard-2-86m` | `openai/gpt-oss-safeguard-20b` | Groq (`https://api.groq.com/openai/v1`) | Lightweight pre-filter before VISION tool execution. |

---

## 2. Detailed Role Breakdown

### A. VISION (Interactive Conversational Supervisor)
- **Active Model (`SA-36`):** `openai/gpt-oss-120b`
- **Responsibilities:**
  - Multilingual student dialogue (**French**, **Fusha**, **Tunisian Derja**, **Arabizi**)
  - RAG-grounded Q&A with mandatory source citations (`1.2_Librarian`)
  - Profile gathering, eligibility explanation (`1.3_Gatekeeper`), shortlist recommendations (`1.5_Advisor`), and pre-inscription handoff (`1.6_Registrar`)
- **Planned Evolution (Sprint 2):** Per `SC V3` scope guard (*fine-tune only one model*), a single QLoRA dialect adapter may be evaluated against this baseline on the 300-utterance Dialect NLU test set.

### B. JARVIS (Asynchronous Scheduling & Follow-Up Orchestrator)
- **Recommended Model:** `openai/gpt-oss-20b`
- **Responsibilities:**
  - `2.1_Scheduler`: Slot proposal wording (actual `FREE → HELD → BOOKED` transitions remain strictly atomic in PostgreSQL — zero double-booking)
  - `2.2_Communicator`: Multilingual reminders (`J-2`, `H-3`) and candidate reply intent classification (`confirm`, `cancel`, `reschedule`, `need_human`)
  - `2.3_Watchdog`: Escalation summaries for RH / NAU Responsable (`ESCALATED` / `DORMANT` transitions)
- **Why a smaller model than VISION:** JARVIS runs asynchronously inside Celery workers at higher volume with narrower, structured prompts; `openai/gpt-oss-20b` optimizes throughput, token budget, and rate-limit headroom.

### C. VLM (Relevé de Notes Extraction — `Bonus-1` & `Bonus-2`)
- **Recommended Model:** `qwen/qwen3.8-27b` (Vision)
- **Responsibilities:**
  - Parse uploaded Tunisian Baccalaureate / university transcripts (image or PDF page render)
  - Return a strict JSON schema: `{bac_section, session_year, general_average, subjects: [{name, grade, coefficient, confidence}], needs_manual_review}`
  - **Validation rule:** Never auto-approve unverified grades; any low-confidence field or unreadable scan triggers manual confirmation in the UI before saving to PostgreSQL (`Bonus-2`).

---

## 3. Provider Strategy (Groq vs. OpenRouter)

- **Groq (`https://api.groq.com/openai/v1`):** Default production/dev runtime for **VISION**, **JARVIS**, **VLM**, and **Whisper STT** under a single OpenAI-compatible API key (ultra-low latency).
- **OpenRouter (`https://openrouter.ai/api/v1`):** Used for:
  1. **Multi-model benchmarking (`SA-33` / `SA-34`)** across candidate models on `backend/tests/data/evaluation_dataset.json`.
  2. **Zero-code-change demo failover** if Groq rate limits are reached (swap `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` in `.env`).

---

## 4. Environment Configuration Reference (`.env`, never commit real keys)

```env
# --- Active SA-36 VISION Backend (implemented in backend/app/core/config.py) ---
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=
LLM_MODEL=openai/gpt-oss-120b
LLM_TEMPERATURE=0.2
LLM_MAX_TOKENS=1024
LLM_TIMEOUT_SECONDS=30.0
LLM_FALLBACK_TO_MOCK=true

# --- Planned Separate Overrides (JARVIS / VLM / STT) ---
# JARVIS_LLM_MODEL=openai/gpt-oss-20b
# VLM_MODEL=qwen/qwen3.8-27b
# STT_MODEL=whisper-large-v3
```

