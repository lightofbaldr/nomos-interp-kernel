"""Explicit opt-in; NEVER run as part of the offline suite. No :8091 access."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness2.substrate.login_tools_control import run  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="fresh audit directory")
    parser.add_argument("--model", required=True, help="operator-selected login model, e.g. opus")
    args = parser.parse_args()
    raise SystemExit(run(args.out, args.model))
