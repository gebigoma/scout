#!/bin/sh
# Stop hook: report anything about the repo state that will bite later.
#
# The scheduled digest job runs against this working copy on Monday morning
# with no human present. Left on a feature branch it now refuses to publish
# (see digest.PUBLISH_BRANCH) - which is safe, but means a silently missed
# week. Left dirty, the next session starts on top of someone else's edits.
#
# Emits a JSON systemMessage for Claude Code, or nothing when clean.
cd "$(git rev-parse --show-toplevel 2>/dev/null)" 2>/dev/null || exit 0

branch=$(git symbolic-ref --quiet --short HEAD 2>/dev/null || echo "a detached HEAD")
dirty=$(git status --porcelain | wc -l | tr -d ' ')
warnings=""

if [ "$branch" != "main" ]; then
	warnings="repo is on '$branch', not main - the Monday digest job will skip publishing"
fi

if [ "$dirty" != "0" ]; then
	[ -n "$warnings" ] && warnings="$warnings; "
	warnings="${warnings}${dirty} uncommitted file(s) in the working tree"
fi

[ -z "$warnings" ] && exit 0

# Escape for JSON: backslashes, then double quotes.
escaped=$(printf '%s' "$warnings" | sed 's/\\/\\\\/g; s/"/\\"/g')
printf '{"systemMessage": "scout hygiene: %s"}\n' "$escaped"
