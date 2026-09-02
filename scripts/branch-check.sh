#!/bin/sh
# SessionStart hook: surface branches that exist only in refs, not on screen.
#
# chore/known-good-recall-log sat unmerged and un-PR'd for two weeks, then was
# re-implemented from scratch as a narrower PR before the original was found
# and merged (see CLAUDE.md). A branch that isn't checked out and has no open
# PR is invisible unless something says so at the start of a session.
#
# Emits a JSON systemMessage for Claude Code, or nothing when everything is
# merged.
#
# Deliberately does not `git fetch`: a network call in session startup stalls
# the session if the remote is slow or unreachable, there's no portable
# timeout(1) to guard it on the macOS system toolchain this repo floors on
# (timeout is gtimeout, from coreutils, not always present), and it isn't
# needed for the case that motivated this - the remote-tracking ref for
# chore/known-good-recall-log was already present locally with no fetch. The
# cost is one-directional staleness: a deleted-but-not-pruned remote branch
# can produce a false positive, which is why the message below carries the
# remedy (`git fetch --prune`) as text instead of running it.
cd "$(git rev-parse --show-toplevel 2>/dev/null)" 2>/dev/null || exit 0

current=$(git symbolic-ref --quiet --short HEAD 2>/dev/null)

list=$(
	{
		git branch -r --no-merged origin/main --format='%(refname:short) %(authordate:short)' 2>/dev/null \
			| grep -v -e '^origin/HEAD ' -e '^origin/main '
		git branch --no-merged main --format='%(refname:short) %(authordate:short)' 2>/dev/null
	} | sed 's#^origin/##' | awk '!seen[$1]++'
)

[ -z "$list" ] && exit 0

entries=""
count=0
while IFS=' ' read -r name date; do
	[ -z "$name" ] && continue
	[ "$name" = "$current" ] && continue
	[ -n "$entries" ] && entries="$entries, "
	entries="$entries$name ($date)"
	count=$((count + 1))
done <<EOF
$list
EOF

[ "$count" -eq 0 ] && exit 0

warnings="$count unmerged branch(es) — ${entries}. Check these before starting new work; run \`git fetch --prune\` if the list looks stale."

# Escape for JSON: backslashes, then double quotes.
escaped=$(printf '%s' "$warnings" | sed 's/\\/\\\\/g; s/"/\\"/g')
printf '{"systemMessage": "scout hygiene: %s"}\n' "$escaped"
