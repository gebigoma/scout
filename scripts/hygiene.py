"""Repo hygiene checks, shared by the git hooks and CI.

The rules live here rather than in the hook scripts so that a check can't
pass locally and fail in CI (or the reverse) because two copies of the same
shell one-liner drifted apart. Stdlib only, same as the pipeline: the hooks
have to run on the macOS system Python with nothing installed.

Each check returns a list of problem strings; empty means clean.
"""
import re
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent

# Claude.dmg (333MB) sat in the working tree for weeks - gitignored, but one
# `git add -A -f` away from being unrecoverable history. Well under any real
# git limit on purpose: nothing this repo legitimately tracks is near it.
MAX_FILE_BYTES = 2 * 1024 * 1024

# Two kinds of thing that must stay out of git, both gitignored, so either can
# only show up staged via an explicit `git add -f`:
#   - generated per-run output, which is scratch rather than deliverable;
#   - the three private files. This is a public repo, and each names specific
#     companies: signals/ ranks them by how close they look to hiring,
#     companies.csv is the watchlist it's computed from, and known_good.csv
#     records reqs worth applying to that the pipeline failed to surface.
#     Keeping the stages but not their artifacts is the point; see digest.run.
NEVER_COMMIT = ("data/raw/", "data/runs/", "logs/", "__pycache__/",
                ".DS_Store", ".claude/settings.local.json", "Claude.dmg",
                "signals/", "data/companies.csv", "data/known_good.csv")

# `worktree-agent-abbbb31faaf724ad2` and friends became PR titles. Require a
# type prefix and a human-readable slug instead.
BRANCH_PATTERN = re.compile(r"^(feat|fix|chore|docs|test|refactor|ci)/[a-z0-9][a-z0-9._-]*$")
EXEMPT_BRANCHES = frozenset({"main"})

# The weekly job commits straight to main by design (when pushed through the
# pre-push hook - see check_main_push for what that does and does not
# guarantee). A digest commit is recognisable by both its subject and the fact
# that it only ever touches published output.
#
# This pattern must stay in sync with exactly the paths digest.run commits.
# When it didn't, the 2026-08-17 run failed: digest committed signals/<date>.md
# while this pattern only allowed `data/signals[\w.-]*\.md`, which could never
# match it - the artifact isn't under data/, and `[\w.-]` excludes `/`. The
# push was rejected as "not a digest commit" after the matches file had already
# been rendered and committed, and digest writes no checkpoint on failure, so
# every later run repeated the rejection. signals/ is no longer published at
# all, which is why it isn't listed here; it's in NEVER_COMMIT instead.
DIGEST_SUBJECT = "Weekly matches:"
DIGEST_PATH_PATTERN = re.compile(r"^(matches/[\d-]+\.md|data/seen\.json)$")

CONFLICT_MARKER = re.compile(r"^(<{7}|={7}|>{7})(\s|$)")


def _git(*args, check=True):
    return subprocess.run(["git", *args], cwd=PROJECT_DIR, check=check,
                          capture_output=True, text=True).stdout


def _is_probably_text(blob: bytes) -> bool:
    return b"\0" not in blob[:8000]


def check_file_sizes(paths, read_bytes) -> list:
    problems = []
    for path in paths:
        size = len(read_bytes(path))
        if size > MAX_FILE_BYTES:
            problems.append(
                f"{path} is {size // 1024}KB, over the {MAX_FILE_BYTES // 1024}KB limit. "
                f"If it's generated, gitignore it; if it's real input, it belongs "
                f"outside git."
            )
    return problems


def check_never_commit(paths) -> list:
    problems = []
    for path in paths:
        for pattern in NEVER_COMMIT:
            if path == pattern or path.startswith(pattern) or f"/{pattern}" in f"/{path}":
                problems.append(f"{path} matches '{pattern}' and must not be committed.")
                break
    return problems


def check_conflict_markers(paths, read_bytes) -> list:
    problems = []
    for path in paths:
        blob = read_bytes(path)
        if not _is_probably_text(blob):
            continue
        for lineno, line in enumerate(blob.decode("utf-8", "replace").splitlines(), 1):
            if CONFLICT_MARKER.match(line):
                problems.append(f"{path}:{lineno} still has a merge conflict marker.")
                break
    return problems


def check_branch_name(branch: str) -> list:
    if branch in EXEMPT_BRANCHES or not branch:
        return []
    if BRANCH_PATTERN.match(branch):
        return []
    return [f"Branch '{branch}' should look like 'fix/short-slug'. "
            f"Rename it with: git branch -m fix/your-slug"]


def check_main_push(commits) -> list:
    """`commits` is a list of (subject, [changed paths]) going to main.

    Only the scheduled digest should land on main without review; anything else
    pushed straight to main skips the PR entirely.

    This is a local guard, not a guarantee. Its only caller is .githooks/
    pre-push, so it catches nothing when the hooks aren't installed or when the
    push uses --no-verify, and CI cannot backstop it - by the time a workflow
    runs, the commit is already on main. Enforcing this for real needs a branch
    protection rule on the remote."""
    problems = []
    for subject, changed in commits:
        if subject.startswith(DIGEST_SUBJECT) and all(
                DIGEST_PATH_PATTERN.match(p) for p in changed):
            continue
        problems.append(f"'{subject}' is not a digest commit - open a PR instead "
                        f"of pushing it straight to main.")
    return problems


# --- git plumbing for the CLI entry points -------------------------------

def _staged_paths() -> list:
    out = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    return [p for p in out.split("\0") if p]


def _staged_bytes(path: str) -> bytes:
    return subprocess.run(["git", "show", f":{path}"], cwd=PROJECT_DIR,
                          check=True, capture_output=True).stdout


def _tracked_paths() -> list:
    return [p for p in _git("ls-files", "-z").split("\0") if p]


def _worktree_bytes(path: str) -> bytes:
    full = PROJECT_DIR / path
    return full.read_bytes() if full.is_file() else b""


def _commits_in_range(rev_range: str) -> list:
    shas = _git("rev-list", rev_range).split()
    commits = []
    for sha in shas:
        subject = _git("log", "-1", "--pretty=%s", sha).strip()
        changed = _git("show", "--pretty=", "--name-only", sha).split()
        commits.append((subject, changed))
    return commits


def _report(problems, header) -> int:
    if not problems:
        return 0
    print(f"\n{header}", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    print("", file=sys.stderr)
    return 1


def main(argv) -> int:
    command = argv[1] if len(argv) > 1 else ""

    if command == "staged":
        paths = _staged_paths()
        problems = (check_never_commit(paths)
                    + check_file_sizes(paths, _staged_bytes)
                    + check_conflict_markers(paths, _staged_bytes))
        return _report(problems, "Blocked this commit:")

    if command == "tracked":
        paths = _tracked_paths()
        problems = (check_never_commit(paths)
                    + check_file_sizes(paths, _worktree_bytes)
                    + check_conflict_markers(paths, _worktree_bytes))
        return _report(problems, "Repo hygiene problems in tracked files:")

    if command == "branch":
        branch = argv[2] if len(argv) > 2 else _git(
            "symbolic-ref", "--quiet", "--short", "HEAD", check=False).strip()
        return _report(check_branch_name(branch), "Branch name:")

    if command == "push-to-main":
        problems = check_main_push(_commits_in_range(argv[2]))
        return _report(problems, "Blocked this push to main:")

    print(__doc__, file=sys.stderr)
    print("usage: hygiene.py {staged|tracked|branch [name]|push-to-main <range>}",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
