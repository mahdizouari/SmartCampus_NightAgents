# SmartCampus AI — LLM Benchmark & Local Open-Source Model Selection Report (`SA-33`)

**Task:** `SA-33` — Compare LLM Candidate Performance and Document Baseline Selection  
**Epic:** `SA-30` — LLM Evaluation & Integration  
**Upstream Dependency:** `SA-32` (`architecture/evaluation_benchmark.md`, `backend/tests/data/evaluation_dataset.json`)  
**Downstream Impact:** `SA-36` (`architecture/llm_models.md`, `backend/app/services/llm_service.py`), `SA-34` (`architecture/dialect_arabizi_evaluation_report.md`), and `ml/colab/` (`01`..`03` Colab Pro notebooks)  
**Owner:** Youssef Trabelsi  

---

## Executive Summary

This report documents the empirical benchmarking harness (`backend/app/services/llm_benchmark_service.py`, `backend/scripts/benchmark_llm_models.py`) and comparative model selection for **SmartCampus AI (IIT / ISB / ISA)**. Built on the official 70-question multilingual evaluation suite (`SA-32`), the harness evaluates open-source language models across French, Modern Standard Arabic (Fusha), Tunisian Derja (Arabic script), Arabizi (Latin script with phonetic digits `3`, `5`, `7`, `9`), and out-of-corpus trap queries.

Following the post-Sprint-1 architectural alignment, SmartCampus AI operates exclusively on **100% self-hosted, open-source models within a `<= 9B` parameter budget** served locally via keyless **Ollama** (`http://host.docker.internal:11434/v1`) and evaluated on Google Colab Pro under a progressive **`0-Shot -> Few-Shot -> QLoRA`** protocol (`ml/colab/01_zero_shot_few_shot_rag_benchmark.ipynb` and `ml/colab/02_qwen_qlora_finetune_gguf_ollama.ipynb`):

- **Primary VISION Supervisor (`1.1_Listener` – `1.6_Registrar`):** **`qwen3:8b`** (`8.2B`, Apache 2.0, `LLM_ENABLE_THINKING=false` injecting `/no_think`) and its single QLoRA 4-bit GGUF fine-tuned adapter **`smartcampus-vision-8b`** (`ml/colab/Modelfile.smartcampus-vision-8b`).
- **JARVIS Asynchronous Orchestrator & VISION Co-Finalist:** **`qwen2.5:7b-instruct`** (`7.6B`, Apache 2.0).
- **Comparative Zero-Shot / Few-Shot Contenders:** **`gemma2:9b`** (`9.2B`) and **`aya-expanse:8b`** (`8.0B`).

In strict adherence to project anti-hallucination governance, **zero benchmark scores are fabricated**: live local Ollama / Colab Pro runs are recorded as **`PENDING_LIVE_RUN`** in `backend/tests/data/llm_benchmark_results.json` until executed on GPU hardware, while earlier Sprint 1 hosted-API runs (`> 9B` cloud models) are preserved strictly in Section 5 as **superseded historical exploration**.

---

## 1. Evaluation Objectives & Acceptance Criteria

1. **Multilingual & Governance Comparison Matrix:** Empirically measure latency (`p50`, `p95`, mean), generation quality across all 4 linguistic registers (French, Fusha, Tunisian Derja, Arabizi), out-of-corpus hallucination refusal, and strict Human-in-the-Loop (HITL) admission compliance (`0` promises of `ADMITTED`, `WAITLIST`, or `REJECTED`, which belong exclusively to the human **NAU Responsable**).
2. **Local Open-Source `<= 9B` Baseline & Single-Adapter Promotion:** Select the local keyless Ollama models for interactive **VISION** (`Visitor -> Candidate`, including prospective students, graduates, and parents/relatives) and asynchronous **JARVIS** (`2.1_Scheduler`..`2.3_Fixer`), and benchmark progressive uplift across `0-Shot`, `Few-Shot`, and single-model `QLoRA` (`smartcampus-vision-8b`) grounded on the 42 official scraped `https://iit.tn/` Markdown documents (`backend/data/corpus/iit/`).

---

## 2. Evaluation Dataset & 100-Point Scoring Rubric (`SA-32`)

The benchmark evaluates the 70-query machine-readable dataset (`backend/tests/data/evaluation_dataset.json`):

| Linguistic Register | Query Count | IDs | Evaluation Focus |
| :--- | :---: | :--- | :--- |
| **Standard French** | 15 | `FR-01` to `FR-15` | IIT/ISB/ISA curricula, prerequisites, Bac+3 bridge tracks, study durations |
| **Modern Standard Arabic (Fusha)** | 15 | `AR-01` to `AR-15` | Formal Arabic grammar, program accuracy, Sfax campus facts |
| **Tunisian Derja (Arabic script)** | 15 | `TN-01` to `TN-15` | Authentic spoken Tunisian register, local phrasing, clarity and accuracy |
| **Arabizi / Tunizi** | 15 | `AB-01` to `AB-15` | Phonetic digits (`3`, `5`, `7`, `9`) and French-Tunisian code-switching |
| **Out-of-Corpus Traps** | 10 | `TR-01` to `TR-10` | Explicit polite refusal without inventing degrees (medicine, law, fake grants) |
| **TOTAL** | **70** | — | Deterministic, reproducible multilingual evaluation |

### Automated & Human-Verified 100-Point Rubric
1. **Linguistic Register Alignment (25 pts):** Strict match with the caller's language/dialect. Answering in formal Fusha when addressed in Tunisian Derja or Arabizi incurs a strict `0/25` penalty.
2. **Factual & RAG Grounding (35 pts):** Fidelity to retrieved `knowledge.chunks` (`BAAI/bge-m3` `1024d` + `TSVECTOR` RRF + `BAAI/bge-reranker-v2-m3`) and PostgreSQL catalog tables (`core.program*`).
3. **Hallucination Resistance (25 pts):** Explicit refusal on `TR-01`..`TR-10` out-of-corpus traps with zero fabricated programs, fees, or deadlines.
4. **HITL Governance & INPDP Privacy (15 pts):** Explicit reminder that admission decisions (`ADMITTED | WAITLIST | REJECTED`) are signed exclusively by the human **NAU Responsable**, plus compliance with Tunisian Law 2004-63 (INPDP).

---

## 3. Active Local Open-Source Candidate Roster (`<= 9B`, Keyless Ollama)

All active models run locally via `LLM_PROVIDER=ollama` (`http://host.docker.internal:11434/v1`) with `$0.00` external API cost. Current benchmark state from `backend/tests/data/llm_benchmark_results.json`:

| Candidate Model | Provider | Parameters | License | Thinking Mode | Status | Overall (/100) | Target Latency (p95) | API Cost / 1k | Active Allocation Verdict |
| :--- | :--- | :---: | :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| `qwen3:8b` | Local Ollama | 8.2B | Apache 2.0 | Disabled (`/no_think` via `LLM_ENABLE_THINKING=false`) | `PENDING_LIVE_RUN` | Pending live GPU run | `< 4.0 s` | `$0.0000` | **Selected Primary (VISION)** |
| `qwen2.5:7b-instruct` | Local Ollama | 7.6B | Apache 2.0 | Standard instruct | `PENDING_LIVE_RUN` | Pending live GPU run | `< 4.0 s` | `$0.0000` | **Selected Co-Finalist (JARVIS / VISION)** |
| `gemma2:9b` | Local Ollama | 9.2B | Gemma Terms | Standard instruct | `PENDING_LIVE_RUN` | Pending live GPU run | `< 4.0 s` | `$0.0000` | **Alternative Benchmark #3** |
| `aya-expanse:8b` | Local Ollama | 8.0B | CC-BY-NC 4.0 | Standard instruct | `PENDING_LIVE_RUN` | Pending live GPU run | `< 4.0 s` | `$0.0000` | **Alternative Benchmark #4** |
| `smartcampus-vision-8b` *(Stage 2 QLoRA)* | Local Ollama (GGUF Q4_K_M) | 8.2B + LoRA | Apache 2.0 | `/no_think` embedded in `Modelfile.smartcampus-vision-8b` | `PENDING_LIVE_RUN` | Pending Colab Pro QLoRA run | `< 4.0 s` | `$0.0000` | **Target Adapted VISION Model** |

### Progressive Colab Pro Protocol (`ml/colab/`)
1. **Stage 0 — Zero-Shot RAG Baseline (`01_zero_shot_few_shot_rag_benchmark.ipynb`):** Evaluate `qwen3:8b` (`/no_think`), `qwen2.5:7b-instruct`, `gemma2:9b`, and `aya-expanse:8b` over the 42 scraped `backend/data/corpus/iit/*.md` documents (`bge-m3` `1024d` + `bge-reranker-v2-m3`) and the 70 `SA-32` + 15 `SA-34` test cases.
2. **Stage 1 — Few-Shot Exemplars (`01_zero_shot_few_shot_rag_benchmark.ipynb`):** Inject curated French / Derja / Arabizi orientation exemplars and measure delta on register alignment and out-of-corpus refusal.
3. **Stage 2 — Single-Model QLoRA 4-Bit Fine-Tuning (`02_qwen_qlora_finetune_gguf_ollama.ipynb`):** Fine-tune a single LoRA adapter on `Qwen/Qwen3-8B` (Unsloth 4-bit), export `Q4_K_M` GGUF, register via `ollama create smartcampus-vision-8b -f ml/colab/Modelfile.smartcampus-vision-8b`, and verify empirical uplift over Stage 0 and Stage 1.
4. **Companion Multimodal Evaluation (`03_vlm_ocr_whisper_benchmark.ipynb`):** Benchmark local `qwen2.5vl:7b` on anonymized Sfax Bac and ISIMS Licence transcripts (`target >= 95%` field exact match) and `whisper-large-v3` on multilingual voice notes.

---

## 4. Technical Justification of the Local `<= 9B` Selection

1. **`qwen3:8b` (`/no_think`) + `smartcampus-vision-8b` (QLoRA) for VISION:**
   - Delivers strong multilingual instruction following across French, Arabic script (Fusha and Tunisian Derja), and Latin Arabizi within an `8.2B` Apache 2.0 footprint that fits comfortably on a single Colab Pro T4/L4 GPU or developer workstation (`~5.2 GB` in Q4_K_M).
   - Setting `LLM_ENABLE_THINKING=false` automatically appends `/no_think` and strips any residual `<think>...</think>` blocks in `llm_service.py` and `llm_benchmark_service.py`, avoiding hidden chain-of-thought token overhead and keeping interactive RAG latency aligned with the `< 4.0 s` p95 target.
2. **`qwen2.5:7b-instruct` for JARVIS (and VISION Fallback):**
   - Apache 2.0 `7.6B` instruct model with fast first-token latency and reliable structured classification for stateless back-office tasks (`2.2_Messenger` template personalization and `confirm | cancel | reschedule | escalate` reply parsing), while all slot locking (`SELECT ... FOR UPDATE`) and timers remain strictly deterministic Python/SQL code.
3. **Why `gemma2:9b` and `aya-expanse:8b` Remain Benchmark Controls Only:**
   - `aya-expanse:8b` carries a non-commercial license (`CC-BY-NC-4.0`) and `gemma2:9b` sits at the upper VRAM edge (`9.2B`); both serve as strong scientific comparison points in Notebook `01` while production defaults stay 100% Apache 2.0 (`qwen3:8b` / `qwen2.5:7b-instruct`).

---

## 5. Archived Appendix — Superseded Sprint 1 Cloud Exploration (`> 9B`, Retired)

> [!NOTE]
> During initial Sprint 1 prototyping — prior to the architectural requirement mandating **100% self-hosted open-source `<= 9B` models with zero external cloud LLM API consumption** — the benchmark harness was exercised on a 25-question stratified subset (`5 FR, 5 Fusha, 5 Derja, 5 Arabizi, 5 Traps`) against hosted Groq endpoints. The figures below are retained **strictly as an archived Sprint 1 historical log** and are **retired from the active platform configuration**.

| Archived Sprint 1 Model | Parameter Class | Lifecycle State | Stratified Score (25/70) | FR (5) | Fusha (5) | Derja (5) | Arabizi (5) | Traps (5) | Latency p50 / p95 | HITL Violations | Disposition |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `qwen/qwen3.8-27b` | 27B (Cloud) | Archived (Sprint 1) | 84.6 | 90.2 | 75.6 | 87.6 | 89.6 | 80.0 | 0.97 s / 1.38 s | 0 / 25 | **Retired** (`> 9B` cloud API; replaced by local `qwen2.5vl:7b` & `qwen3:8b`) |
| `openai/gpt-oss-20b` | 20B (Cloud) | Archived (Sprint 1) | 83.0 | 87.8 | 80.6 | 92.6 | 79.0 | 75.0 | 0.78 s / 1.11 s | 0 / 25 | **Retired** (`> 9B` cloud API; replaced by local `qwen2.5:7b-instruct`) |
| `openai/gpt-oss-120b` | 120B (Cloud) | Archived (Sprint 1) | 81.5 | 84.6 | 80.6 | 92.6 | 69.6 | 80.0 | 1.48 s / 1.66 s | 0 / 25 | **Retired** (`> 9B` cloud API; replaced by local `qwen3:8b` `/no_think` & `smartcampus-vision-8b`) |

---

## 6. Reproducibility & Execution Commands

Run the benchmark locally against keyless Ollama (no external API key required):

```bash
# 1. Ensure local Ollama models are pulled on the host
ollama pull qwen3:8b
ollama pull qwen2.5:7b-instruct
ollama pull gemma2:9b
ollama pull aya-expanse:8b

# 2. Run a quick 5-query smoke benchmark via Docker (keyless local Ollama)
docker compose exec -T backend python scripts/benchmark_llm_models.py --limit 5

# 3. Run the full 70-query SA-32 benchmark and regenerate JSON/Markdown summaries
docker compose exec -T backend python scripts/benchmark_llm_models.py

# 4. Run offline unit tests (AsyncMock & rubric verification, zero GPU/network needed)
docker compose exec -T backend pytest tests/test_llm_benchmark.py -v
```
