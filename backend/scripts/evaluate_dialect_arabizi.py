"""CLI Runner for SA-34 Tunisian Derja, Arabizi & Code-Switching Evaluation.

Usage inside Docker (recommended):
  docker compose exec backend python scripts/evaluate_dialect_arabizi.py --mode pipeline
  docker compose exec backend python scripts/evaluate_dialect_arabizi.py --mode live-llm --limit 15
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Ensure /app (backend root) is on sys.path when invoked as `python scripts/...`
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.services.dialect_eval_service import (  # noqa: E402
    PipelineEvaluationReport,
    evaluate_live_llm,
    evaluate_pipeline,
)


def _print_pipeline_summary(report: PipelineEvaluationReport) -> None:
    print("=" * 88)
    print("  SmartCampus AI — SA-34 Dialect, Arabizi & Code-Switching Evaluation Report")
    print(
        f"  Dataset: {report.total_cases} cases "
        f"({report.sa32_cases} SA-32 official + {report.stress_cases} SA-34 stress cases)"
    )
    print("=" * 88)
    print(
        f"{'Language Group':26s} | {'N':>2s} | "
        f"{'SA-36 Base Lang':>15s} | {'SA-34 Lang':>10s} | "
        f"{'SA-36 Base Int':>14s} | {'SA-34 Int':>9s} | {'SA-34 FB':>8s}"
    )
    print("-" * 88)
    tuned_map = {g.language_group: g for g in report.tuned_by_group}
    for bg in report.baseline_by_group:
        tg = tuned_map[bg.language_group]
        print(
            f"{bg.language_group:26s} | {bg.total:2d} | "
            f"{bg.lang_correct:2d}/{bg.total:2d} ({bg.lang_accuracy_pct:5.1f}%) | "
            f"{tg.lang_accuracy_pct:8.1f}%  | "
            f"{bg.intent_correct:2d}/{bg.total:2d} ({bg.intent_accuracy_pct:5.1f}%) | "
            f"{tg.intent_accuracy_pct:7.1f}%  | {tg.fallback_accuracy_pct:6.1f}%"
        )
    print("-" * 88)
    print(
        f"{'OVERALL':26s} | {report.total_cases:2d} | "
        f"{report.baseline_overall_lang_pct:14.1f}% | "
        f"{report.tuned_overall_lang_pct:8.1f}%  | "
        f"{report.baseline_overall_intent_pct:13.1f}% | "
        f"{report.tuned_overall_intent_pct:7.1f}%  | {report.tuned_overall_fallback_pct:6.1f}%"
    )
    print("=" * 88)
    print(
        f"[OK] SA-36 Baseline Failures : {report.baseline_failure_count}/{report.total_cases}"
    )
    print(
        f"[OK] SA-34 Tuned Remaining Failures : {report.tuned_failure_count}/{report.total_cases}"
    )
    print("=" * 88)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate SmartCampus AI dialect, Arabizi, and FR/TN code-switching handling."
    )
    parser.add_argument(
        "--mode",
        choices=["pipeline", "live-llm"],
        default="pipeline",
        help="Evaluation mode: 'pipeline' (deterministic SA-36 vs SA-34) or 'live-llm'.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional max number of cases to evaluate in live-llm mode.",
    )
    parser.add_argument(
        "--json-out",
        type=str,
        default=None,
        help="Optional path to export machine-readable JSON results.",
    )
    args = parser.parse_args()

    if args.mode == "pipeline":
        report = evaluate_pipeline()
        _print_pipeline_summary(report)
        if args.json_out:
            out_path = Path(args.json_out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"[OK] JSON results written to {out_path}")
        return 0

    # live-llm mode
    is_openai_compat = settings.LLM_PROVIDER.lower().strip() == "openai_compatible"
    if is_openai_compat and not settings.LLM_API_KEY.strip():
        print(
            "[INFO] LLM_API_KEY is not configured in .env; running live-llm evaluation in safe "
            "fallback mode (set LLM_API_KEY in root .env for live Groq/OpenRouter execution)."
        )
    live_res = asyncio.run(evaluate_live_llm(limit=args.limit))
    print(json.dumps({k: v for k, v in live_res.items() if k != "items"}, indent=2))
    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(live_res, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[OK] Live-LLM JSON results written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
