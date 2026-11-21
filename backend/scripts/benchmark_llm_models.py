"""CLI Runner for SA-33 Live LLM Models Benchmark & Comparative Evaluation.

Usage:
    cd backend
    # Run benchmark report (Pending live run if LLM_API_KEY is not set):
    python scripts/benchmark_llm_models.py

    # Run live evaluation with explicit API key:
    python scripts/benchmark_llm_models.py --api-key gsk_your_key_here --limit 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

# Ensure backend root is on sys.path
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.services.llm_benchmark_service import (  # noqa: E402
    run_full_benchmark,
)


async def main_async() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark candidate LLM models on SA-32 dataset (SA-33)."
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="Optional LLM API key (Groq / OpenRouter) for live execution.",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=None,
        help="Optional LLM base URL (default: settings.LLM_BASE_URL).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional max questions to evaluate per model (for quick sanity checks).",
    )
    parser.add_argument(
        "--json-out",
        type=str,
        default="tests/data/llm_benchmark_results.json",
        help="Path to output JSON benchmark results.",
    )
    args = parser.parse_args()

    effective_key = args.api_key or settings.LLM_API_KEY

    print("=" * 115)
    print("  SmartCampus AI — SA-33 Live Benchmark & Sélection des Modèles LLM")
    print("  Dataset de test : SA-32 (backend/tests/data/evaluation_dataset.json — 70 questions)")
    print("  Grille officielle : architecture/evaluation_benchmark.md (100 points)")
    status_str = "OUI (Live API activée)" if effective_key else "NON (Mode En attente)"
    print(f"  Clé API configurée : {status_str}")
    print("=" * 115)

    report = await run_full_benchmark(
        api_key=args.api_key,
        base_url=args.base_url,
        limit=args.limit,
    )

    print("\n" + report.markdown_comparison_table + "\n")
    print("=" * 115)
    print(f"[*] Modèle sélectionné pour VISION (SA-36) : {report.recommended_vision_model}")
    print(f"[*] Modèle sélectionné pour JARVIS (SA-36) : {report.recommended_jarvis_model}")
    print("=" * 115)

    if args.json_out:
        out_path = Path(args.json_out)
        if not out_path.is_absolute():
            out_path = BACKEND_ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(asdict(report), f, ensure_ascii=False, indent=2)
        print(f"[OK] Résultats exportés avec succès vers : {out_path}")

    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
