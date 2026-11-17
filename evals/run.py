"""Evaluation runner. Same as `repro eval`; this script only exists as an entry point.

Usage, from the repository root:

    uv run python evals/run.py --model gemini-2.5-flash --cases centavo_a_mais,cupom_acima_de_100

The logic lives in src/repro_agent/evaluation.py, so the CLI and this script share it.
"""

import sys

from repro_agent.cli import app

if __name__ == "__main__":
    app(["eval", *sys.argv[1:]], prog_name="evals/run.py")
