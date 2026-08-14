#!/bin/sh
# One-time setup: point git at the hooks committed in .githooks/.
#
# core.hooksPath is used instead of copying files into .git/hooks so the hooks
# are versioned, reviewable, and update themselves on pull. It's per-clone
# config, so run this once per clone (and once per worktree checkout that
# doesn't share the parent's config).
set -e
root="$(git rev-parse --show-toplevel)"
chmod +x "$root/.githooks/"*
git -C "$root" config core.hooksPath .githooks
echo "Hooks installed. Bypass a blocked commit or push with --no-verify."
