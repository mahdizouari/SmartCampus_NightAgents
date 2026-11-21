"""CLI Runner for SA-33 LLM Models Benchmark & Comparative Evaluation.

Usage:
    cd backend
    python scripts/benchmark_llm_models.py
    python scripts/benchmark_llm_models.py --json-out tests/data/llm_benchmark_results.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

# Ensure backend root is on sys.path
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.llm_benchmark_service import (  # noqa: E402
    run_full_benchmark,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark candidate LLM models on SA-32 dataset (SA-33)."
    )
    parser.add_argument(
        "--json-out",
        type=str,
        default="tests/data/llm_benchmark_results.json",
        help="Path to output JSON benchmark results.",
    )
    args = parser.parse_args()

    print("=" * 110)
    print("  SmartCampus AI — SA-33 Benchmark & Sélection des Modèles LLM")
    print("  Dataset de test : SA-32 (backend/tests/data/evaluation_dataset.json — 70 questions)")
    print("  Grille officielle : architecture/evaluation_benchmark.md (100 points)")
    print("=" * 110)

    report = run_full_benchmark()

    print("\n" + report.markdown_comparison_table + "\n")
    print("=" * 110)
    print(f"[*] Modèle sélectionné pour VISION (SA-36) : {report.recommended_vision_model}")
    print(f"[*] Modèle sélectionné pour JARVIS (SA-36) : {report.recommended_jarvis_model}")
    print("=" * 110)

    if args.json_out:
        out_path = Path(args.json_out)
        if not out_path.is_absolute():
            out_path = BACKEND_ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(asdict(report), f, ensure_ascii=False, indent=2)
        print(f"[OK] Résultats exportés avec succès vers : {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
