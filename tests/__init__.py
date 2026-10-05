"""Test suite for the scout pipeline.

`scripts/` is put on sys.path here so tests can `import pipeline` and
`import run_pipeline` exactly the way the orchestrator does, without the
project needing to be installed as a package.
"""
import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Variables that tell git which repository to act on, overriding the cwd every
# test helper passes. Inherited from the shell that launched the suite, they
# point every `git init`, `git config` and `git commit` the tests run at that
# repository instead of a temp dir. That happened on 2026-10-05: a session
# ran the suite from a linked worktree with these set, and the helpers in
# support.init_repo_with_remote re-initialised the real repo as bare, rewrote
# its user.name/user.email to "scout tests <test@example.com>", and committed
# "initial" and "a" onto that worktree's branch. The identity lived only in
# the repo's local config, so every later commit - the scheduled digest
# included - would have been authored as the test fixture. Stripped once,
# here, at import, so no test and no helper can opt back into it.
GIT_LOCATION_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                     "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                     "GIT_NAMESPACE", "GIT_PREFIX")
for _var in GIT_LOCATION_VARS:
    os.environ.pop(_var, None)
