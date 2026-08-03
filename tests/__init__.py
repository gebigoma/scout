"""Test suite for the scout pipeline.

`scripts/` is put on sys.path here so tests can `import pipeline` and
`import run_pipeline` exactly the way the orchestrator does, without the
project needing to be installed as a package.
"""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
