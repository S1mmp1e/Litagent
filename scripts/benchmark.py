"""Run the deterministic synthetic retrieval benchmark without downloading models."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
env = os.environ.copy()
env["LIT_BENCHMARK"] = "1"
raise SystemExit(subprocess.call(
    [sys.executable, "-m", "pytest", "-q", "-s", "tests/test_benchmark.py"], cwd=ROOT, env=env,
))
