#!/usr/bin/env bash
# Build the single-file bundle and publish it to the dist-bundle branch as
# rtltracer-v<schema>.py. One branch, one file per schema version: a rebuild at
# the same version overwrites that file, other versions are carried forward.
# Skips the push when the bundle is unchanged. Usage: publish-bundle.sh [remote]
set -euo pipefail
cd "$(dirname "$0")/.."

remote="${1:-origin}"
branch="dist-bundle"

python3 scripts/bundle.py
schema=$(python3 -c "from rtltracer.db import SCHEMA_VERSION; print(SCHEMA_VERSION)")
file="rtltracer-v${schema}.py"

# Base the new commit on the current remote tip, so other files survive.
git fetch -q "$remote" "+refs/heads/${branch}:refs/remotes/${remote}/${branch}" 2>/dev/null || true
parent=$(git rev-parse -q --verify "refs/remotes/${remote}/${branch}" \
      || git rev-parse -q --verify "refs/heads/${branch}" || true)

blob=$(git hash-object -w dist/rtltracer.py)
index=$(mktemp); rm -f "$index"; export GIT_INDEX_FILE="$index"
[ -n "$parent" ] && git read-tree "${parent}^{tree}"
git update-index --add --cacheinfo "100644,${blob},${file}"
tree=$(git write-tree)
unset GIT_INDEX_FILE; rm -f "$index"

if [ -n "$parent" ] && [ "$(git rev-parse "${parent}^{tree}")" = "$tree" ]; then
    echo "${file} unchanged; dist-bundle already current"
    exit 0
fi

parent_arg=""; [ -n "$parent" ] && parent_arg="-p $parent"
commit=$(git commit-tree "$tree" $parent_arg -m "bundle: ${file} (schema v${schema})")
git push "$remote" "${commit}:refs/heads/${branch}"
echo "published ${file} to ${branch} (${commit})"
