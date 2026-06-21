"""EPISTEME CLI entry point."""

import argparse
from dotenv import load_dotenv

load_dotenv()

from episteme.config import VALID_CASES
from episteme.pipeline import run_pipeline


def main():
    parser = argparse.ArgumentParser(description="EPISTEME — Epistemic Analysis System")
    parser.add_argument("--case", required=True, choices=VALID_CASES)
    parser.add_argument(
        "--step",
        default="all",
        choices=[
            "ingest",
            "reconcile",
            "structure",
            "crystallize",
            "importance",
            "relate",
            "debate",
            "hypothesis",
            "reasoning",
            "assess",
            "methodology",
            "all",
        ],
    )
    parser.add_argument("--reset-cache", action="store_true")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--max-chunks", type=int, default=None, metavar="N")
    args = parser.parse_args()

    chunks_label = f"max-chunks: {args.max_chunks}" if args.max_chunks else "all chunks"
    print(f"\nEPISTEME — case: {args.case} | step: {args.step} | {chunks_label}\n")
    run_pipeline(
        case=args.case,
        step=args.step,
        reset_cache=args.reset_cache,
        demo_mode=args.demo,
        max_chunks=args.max_chunks,
    )


if __name__ == "__main__":
    main()
