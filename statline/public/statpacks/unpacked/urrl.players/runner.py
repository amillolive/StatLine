from pathlib import Path

from statline.sdk import run_statpack

raise SystemExit(run_statpack(Path(__file__).resolve().parent))
