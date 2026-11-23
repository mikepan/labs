"""
eval.unittests.run - Test runner with fast and full modes.

Usage:
    uv run test                                          # Fast pipeline tests
    uv run test full                                     # Adds the live reasoning matrix
    uv run test full --models all --samples 5 --max-tokens 4096  # Full live matrix
"""

from __future__ import annotations

import argparse
import sys
import unittest

UNIT_MODULE = "eval.unittests.test_eval_pipeline"
LIVE_MODULE = "eval.unittests.reasoning_live"


def build_suite(
    mode: str, models: str, samples: int, keep_alive: bool, max_tokens: int
) -> unittest.TestSuite:
    """Build the requested test suite, adding live tests only in full mode."""
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName(UNIT_MODULE)
    if mode == "full":
        from eval.unittests import reasoning_live

        reasoning_live.configure(
            models=models, samples=samples, keep_alive=keep_alive, max_tokens=max_tokens
        )
        suite.addTests(loader.loadTestsFromName(LIVE_MODULE))
    return suite


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the eval test suite.")
    parser.add_argument(
        "mode",
        nargs="?",
        default="unit",
        choices=("unit", "full"),
        help="'unit' (default) runs fast tests; 'full' adds the live reasoning matrix",
    )
    parser.add_argument(
        "--models",
        default="served",
        help="Live matrix model scope: 'served' (default), 'all', or a comma-separated filter",
    )
    parser.add_argument("--samples", type=int, default=5, help="Samples per prompt (default: 5)")
    parser.add_argument(
        "--max-tokens", type=int, default=4096, help="Max output tokens per turn (default: 4096)"
    )
    parser.add_argument("--keep-alive", action="store_true", help="Leave launched vLLM containers running")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose test output")
    args = parser.parse_args(argv)

    suite = build_suite(args.mode, args.models, args.samples, args.keep_alive, args.max_tokens)
    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
