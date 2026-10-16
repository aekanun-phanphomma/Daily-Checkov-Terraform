"""End-to-end with the recorded S3 answer: real terraform, real Checkov.

Slow (provider download + three Checkov runs + Chromium), so opt-in:
RUN_SLOW=1 pytest
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(not os.environ.get("RUN_SLOW"),
                                reason="set RUN_SLOW=1 for the end-to-end run")


def test_s3_fixture_passes_every_gate(tmp_path):
    env = {**os.environ, "LLM_FIXTURE_DIR": str(ROOT / "tests/fixtures/s3")}
    proc = subprocess.run(
        [sys.executable, "src/generate.py", "--dir", str(tmp_path), "--rule", "CKV_AWS_53"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, proc.stderr[-3000:]
    meta = json.loads((tmp_path / "meta.json").read_text())
    assert meta["guideline_url"].startswith("https://")
    assert (tmp_path / "card.png").stat().st_size > 10_000
    # LinkedIn rejects videos under 75 KB.
    assert (tmp_path / "card.mp4").stat().st_size > 75_000
    assert not (tmp_path / "bad" / ".terraform").exists()
