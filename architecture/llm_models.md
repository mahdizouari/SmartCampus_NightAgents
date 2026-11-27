# SmartCampus AI — Local Open-Source LLM, VLM, Embedding & Speech Allocation

This document defines the local-first, open-source (`<= 9B` parameter) AI model allocation across **VISION**, **JARVIS**, **VLM (`1.4_Reader`)**, **RAG Embeddings / Reranking (`1.2_Librarian`)**, and **Speech-to-Text (`1.1_Listener`)** in SmartCampus AI.

> **Governance Reminder (Non-Negotiable):**
> - **HITL Admissions:** No LLM or VLM may admit, waitlist, or reject a candidate (`ADMITTED | WAITLIST | REJECTED`). Final decisions belong exclusively to the human **Responsable** (`NAU Responsable`).
> - **No Hallucinations:** Every academic fact (fees, deadlines, admission thresholds, prerequisites, program titles) must come from retrieved official corpus passages (`knowledge.chunks`) or PostgreSQL catalog tables maintained by the Responsable.
> - **Empirical Progression (Colab Pro):** Model promotion follows a strict **`0-Shot -> Few-Shot -> QLoRA`** protocol evaluated on `backend/tests/data/evaluation_dataset.json` (`SA-32` / `SA-33` / `SA-34`). No metrics are fabricated before live runs complete.

---

## 1. Local Open-Source Model Allocation (`<= 9B` Parameter Budget)

| Component | Agent / Scope | Primary Local Model | Co-Finalist / Fallback | Runtime Provider (`Base URL`) | Why This Model |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **VISION** | Visitor / Candidate Orientation & Pre-Registration (`1.1_Listener` – `1.6_Registrar`) | `qwen3:8b` (8.2B, Apache 2.0) or fine-tuned `smartcampus-vision-8b` (QLoRA Q4_K_M GGUF) | `qwen2.5:7b-instruct` (7.6B, Apache 2.0); `mock` offline CI fallback | Local Ollama (`http://host.docker.internal:11434/v1`, keyless) | Strong multilingual coverage (French, Fusha, Tunisian Derja, Arabizi) within `<= 9B`; setting `LLM_ENABLE_THINKING=false` automatically injects `/no_think` for both `qwen3*` and `smartcampus-vision*` to meet the interactive `< 4 s` RAG target latency. |
| **JARVIS** | Asynchronous Scheduling & Follow-Up (`2.1_Scheduler`, `2.2_Messenger`, `2.3_Fixer`) | `qwen2.5:7b-instruct` (7.6B, Apache 2.0) | `qwen3:8b` (`/no_think`) | Local Ollama (`http://host.docker.internal:11434/v1`, keyless) | Fast, structured JSON & template-bounded multilingual drafting for reminders and reply classification (`confirm \| cancel \| reschedule \| escalate`). Deterministic slot locks and timers remain strictly in PostgreSQL + Celery. |
| **VLM** | Official Grade Transcript OCR & JSON Extraction (`1.4_Reader`) | `qwen2.5vl:7b` (7B, Apache 2.0) | `qwen3-vl:8b`; deterministic local PDF parser fallback (`pypdf` + arithmetic check) | Local Ollama (`VLM_PROVIDER=ollama`, keyless) | Open-weight vision-language extraction returning strict JSON (`bac_section`, `general_average`, `subjects`, per-field confidence `< 0.85` flagging) with zero cloud API key requirement. |
| **RAG Embeddings** | Official Corpus Vector Indexing (`1.2_Librarian`) | `bge-m3` (`BAAI/bge-m3`, `1024d`, MIT) | Deterministic mock embeddings in offline CI | Local Ollama (`EMBEDDING_PROVIDER=ollama`, keyless) | Multilingual dense embeddings (`1024d`) aligned with `knowledge.chunks.embedding vector(1024)` + HNSW cosine index (`m=16, ef_construction=64`), paired with PostgreSQL `TSVECTOR` full-text search (RRF). |
| **RAG Reranker** | Cross-Encoder Passage Reranking (`1.2_Librarian`) | `BAAI/bge-reranker-v2-m3` (568M, Apache 2.0) | RRF lexical/dense score fallback | Local Python / HuggingFace runtime | Multilingual cross-encoder reranking before CRAG relevance gating (`RAG_MIN_RELEVANCE_SCORE = 0.25`). |
| **STT** | Multilingual Voice Notes (`1.1_Listener`) | `whisper-large-v3` (local `faster-whisper` / OpenAI-compatible server) | `openai_compatible` or `mock` fallback (`WHISPER_PROVIDER`) | `WHISPER_PROVIDER=local` (keyless `WHISPER_BASE_URL`) | Transcribes French, Tunisian Derja, and Arabic code-switching voice notes with mandatory user review before invoking VISION. |

---

## 2. Latency Optimization & QLoRA Fine-Tuning Workflow

### 2.1 Qwen3 `/no_think` Fast-Path Optimization
- Qwen3-8B generates hidden `<think>...</think>` reasoning blocks by default, which can push interactive RAG turns above the `< 4 s` p95 target on local GPUs.
- `backend/app/core/config.py` defaults to `LLM_ENABLE_THINKING=False`.
- Both `backend/app/services/llm_service.py` and `backend/app/services/llm_benchmark_service.py` automatically append `/no_think` to the system message whenever `LLM_PROVIDER=ollama`, `LLM_ENABLE_THINKING=false`, and the active model name matches `qwen3` or `smartcampus-vision`, while stripping any residual `<think>` tags before returning the answer.
- `ml/colab/Modelfile.smartcampus-vision-8b` embeds `/no_think` in its default `SYSTEM` directive for direct Ollama CLI smoke testing.

### 2.2 Progressive Evaluation & Single-Adapter QLoRA (`ml/colab/`)
Per the project scope guard (*fine-tune at most one model*), fine-tuning is restricted to the single VISION conversational adapter trained on Google Colab Pro and exported as Q4_K_M GGUF:
1. **Stage 0 — Zero-Shot Baseline:** Evaluate `qwen3:8b` (`/no_think`), `qwen2.5:7b-instruct`, `gemma2:9b`, and `aya-expanse:8b` on the 70-question multilingual + out-of-corpus trap suite (`SA-32` / `SA-33`).
2. **Stage 1 — Few-Shot Prompting:** Add grounded French / Derja / Arabizi orientation exemplars and measure uplift on dialect register alignment and hallucination resistance.
3. **Stage 2 — QLoRA 4-bit Fine-Tuning (`smartcampus-vision-8b`):** Train a single LoRA adapter on Colab Pro (Unsloth / PEFT), export to `unsloth.Q4_K_M.gguf`, package via `ollama create smartcampus-vision-8b -f ml/colab/Modelfile.smartcampus-vision-8b`, and compare head-to-head against the Zero-Shot and Few-Shot baselines.

---

## 3. Progressive RAG Strategy (`1.2_Librarian`)

The knowledge retrieval pipeline progresses incrementally and is empirically evaluated at each stage:
1. **Classic Dense RAG:** `bge-m3` (`1024d`) cosine similarity on `knowledge.chunks` (`pgvector` HNSW).
2. **Hybrid RAG (Dense + Lexical RRF):** Reciprocal Rank Fusion combining `pgvector` cosine distance and PostgreSQL `TSVECTOR` full-text search across scraped official `iit.tn` pages and curated NAU corpus documents.
3. **Cross-Encoder Reranking & CRAG Gating:** Re-score top candidates with `BAAI/bge-reranker-v2-m3` and gate generation on `RAG_MIN_RELEVANCE_SCORE` so unsupported queries trigger honest refusal rather than hallucination.
4. **Structured SQL BDR + Deterministic Business Rules:** Combine RAG passages with live PostgreSQL catalog queries (`core.program`, `core.program_prerequisite`, `core.program_fee`, `core.admission_campaign`) and YAML eligibility rules.

---

## 4. Environment Configuration Reference (`.env`, never commit secrets)

```env
# --- Local Ollama Open-Source <=9B Runtime (backend/app/core/config.py) ---
LLM_PROVIDER=ollama
LLM_BASE_URL=http://host.docker.internal:11434/v1
LLM_API_KEY=
LLM_MODEL=qwen3:8b
LLM_ENABLE_THINKING=false
LLM_TEMPERATURE=0.2
LLM_MAX_TOKENS=1024
LLM_FALLBACK_TO_MOCK=true

# --- Local VLM (1.4_Reader) ---
VLM_PROVIDER=ollama
VLM_BASE_URL=
VLM_API_KEY=
VLM_MODEL=qwen2.5vl:7b

# --- Local Embeddings & Reranker (1.2_Librarian, vector(1024)) ---
EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=bge-m3
EMBEDDING_DIMENSION=1024
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
RAG_MIN_RELEVANCE_SCORE=0.25

# --- Whisper STT (1.1_Listener: local keyless | openai_compatible | mock) ---
WHISPER_PROVIDER=local
WHISPER_BASE_URL=http://host.docker.internal:8001/v1
WHISPER_MODEL=whisper-large-v3
```
