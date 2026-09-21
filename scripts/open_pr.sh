#!/usr/bin/env bash
# Commit the given paths to a NEW branch and open a pull request against $BASE_BRANCH.
# Used by the import workflows. It never commits to, or pushes, the base branch.
#
#   open_pr.sh <branch-prefix> <title> <body-file> <path>...
#
# Environment: BASE_BRANCH (required), GH_TOKEN (required), GITHUB_RUN_ID (optional).
set -euo pipefail

prefix=$1
title=$2
body_file=$3
shift 3
: "${BASE_BRANCH:?BASE_BRANCH is required}"
: "${GH_TOKEN:?GH_TOKEN is required}"
branch="${prefix}-${GITHUB_RUN_ID:-$(date -u +%Y%m%d%H%M%S)}"

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

git switch -c "$branch"
if [ "$(git branch --show-current)" = "$BASE_BRANCH" ]; then
  echo "refusing to commit on $BASE_BRANCH" >&2
  exit 1
fi

git add -- "$@"
if git diff --cached --quiet; then
  echo "nothing to commit"
  exit 0
fi
git commit --quiet -m "$title"
git push --quiet origin "HEAD:refs/heads/$branch"
gh pr create --base "$BASE_BRANCH" --head "$branch" --title "$title" --body-file "$body_file"
echo "branch=$branch" >> "${GITHUB_OUTPUT:-/dev/null}"
