#!/usr/bin/env bash
# Pulls new commits from a course-build repo into its portfolio folder, with full history, but
# without that repo's course_context/ folder (the course's lesson text, © Vercel, which stays in
# the private repo only).
#
# Deleting course_context/ after an ordinary `git subtree pull` is not enough: the pulled commits
# themselves contain the lesson files, and pushing them publishes them. So this script first makes
# a throwaway clone of the source repo with course_context/ removed from every commit
# (git filter-repo), then pulls from that clone. Commits that never touched course_context/ keep
# their hashes, so the history already in the portfolio lines up with the filtered one.
#
# Usage: scripts/sync_course_build.sh <portfolio-folder> <source-repo-path> [branch]
#   scripts/sync_course_build.sh vercel_coding_harness ../vercel-coding-harness
#
# The script commits locally and never pushes; review with `git log` and push yourself.
set -euo pipefail

prefix=${1:?portfolio folder, e.g. vercel_coding_harness}
source_repo=$(cd "${2:?path to the source repo}" && pwd)
branch=${3:-main}
excluded=course_context

cd "$(git rev-parse --show-toplevel)"
if [ -n "$(git status --porcelain)" ]; then
  echo "Working tree has uncommitted changes; commit or stash them first." >&2
  exit 1
fi

# git filter-repo: use the installed one, otherwise install it into a cached virtualenv.
filter_repo=$(command -v git-filter-repo || true)
if [ -z "$filter_repo" ]; then
  venv="${TMPDIR:-/tmp}/portfolio-filter-repo"
  [ -x "$venv/bin/git-filter-repo" ] || { python3 -m venv "$venv" && "$venv/bin/pip" install -q git-filter-repo; }
  filter_repo="$venv/bin/git-filter-repo"
fi

# Filtered throwaway clone: same commits, minus every file under course_context/. Commits that
# only touched course_context/ become empty and are dropped.
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
git clone -q --no-local --single-branch --branch "$branch" "$source_repo" "$work/src"
(cd "$work/src" && "$filter_repo" --quiet --force --path "$excluded/" --invert-paths)

before=$(git rev-parse HEAD)
git subtree pull -q --prefix="$prefix" "$work/src" "$branch" \
  -m "Merge $(basename "$source_repo") into $prefix/ (without $excluded/)"

# Backstop: nothing reachable from HEAD may contain the excluded folder. On failure, undo the pull.
if [ -n "$(git log --oneline HEAD -- "$prefix/$excluded")" ]; then
  git reset -q --hard "$before"
  echo "Pull would have added $prefix/$excluded to history; reset to $before." >&2
  exit 1
fi

if [ "$(git rev-parse HEAD)" = "$before" ]; then
  echo "$prefix/ is already up to date."
else
  echo "Pulled into $prefix/:"
  git log --oneline "$before"..HEAD
  echo "Review, then: git push"
fi
