#!/usr/bin/env bash
# Build the single-file bundle and publish it to the dist-bundle branch as
# rtltracer-v<schema>.py. One branch, one file per schema version; existing
# files are carried forward. Usage: scripts/publish-bundle.sh [remote]
set -euo pipefail
cd "$(dirname "$0")/.."

remote="${1:-origin}"
branch="dist-bundle"

python3 scripts/bundle.py
schema=$(python3 -c "from rtltracer.db import SCHEMA_VERSION; print(SCHEMA_VERSION)")
file="rtltracer-v${schema}.py"

blob=$(git hash-object -w dist/rtltracer.py)
parent=$(git rev-parse -q --verify "refs/heads/${branch}" || true)

index=$(mktemp)
rm -f "$index"                      # let git create a fresh index at this path
export GIT_INDEX_FILE="$index"
[ -n "$parent" ] && git read-tree "${parent}^{tree}"
git update-index --add --cacheinfo "100644,${blob},${file}"
tree=$(git write-tree)
unset GIT_INDEX_FILE
rm -f "$index"

msg="bundle: ${file} (schema v${schema})"
if [ -n "$parent" ]; then
    commit=$(git commit-tree "$tree" -p "$parent" -m "$msg")
else
    commit=$(git commit-tree "$tree" -m "$msg")
fi
git update-ref "refs/heads/${branch}" "$commit"
git push "$remote" "${branch}:${branch}"
echo "published ${file} to ${branch} (${commit})"
