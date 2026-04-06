#!/bin/bash
set -euo pipefail

# LaunchAgent runs with minimal PATH — set explicitly
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export LANG="en_US.UTF-8"
# Use macOS launchd SSH agent (auto-set by the system)
export SSH_AUTH_SOCK=$(launchctl getenv SSH_AUTH_SOCK 2>/dev/null || echo "/private/tmp/com.apple.launchd.*/Listeners")

REPO="$HOME/Projects/personal_journal_feed"
cd "$REPO"

DATE=$(TZ=America/Los_Angeles date +%Y-%m-%d)
MONTH=$(TZ=America/Los_Angeles date +%Y/%m)

echo "=== Journal Digest: $DATE ==="
echo "Started at $(date)"

# Sync with remote
git pull --rebase origin main

# Generate digest
mkdir -p "$MONTH"
/usr/local/bin/python3 generate.py --output "$MONTH/$DATE.html"

# Commit to main
git add "$MONTH/$DATE.html" "$MONTH/latest.json" index.html latest.json 2>/dev/null || true
if ! git diff --cached --quiet; then
    git commit -m "digest: $DATE"
    git push origin main
    echo "Pushed to main"
else
    echo "No changes to commit on main"
fi

# Deploy to gh-pages
TMP=$(mktemp -d)
cp "$MONTH/$DATE.html" "$TMP/digest.html"
[ -f "$MONTH/latest.json" ] && cp "$MONTH/latest.json" "$TMP/latest.json"
cp .nojekyll "$TMP/.nojekyll"

git fetch origin gh-pages 2>/dev/null || true
git checkout -B gh-pages origin/gh-pages 2>/dev/null || git checkout --orphan gh-pages

mkdir -p "$MONTH"
cp "$TMP/digest.html" "$MONTH/$DATE.html"
[ -f "$TMP/latest.json" ] && cp "$TMP/latest.json" "$MONTH/latest.json"
printf '<!DOCTYPE html>\n<html><head>\n<meta http-equiv="refresh" content="0;url=%s/%s.html">\n</head><body>\n<p>Redirecting to <a href="%s/%s.html">today'\''s digest</a>...</p>\n</body></html>\n' \
    "$MONTH" "$DATE" "$MONTH" "$DATE" > index.html
cp "$TMP/.nojekyll" .nojekyll

git add index.html .nojekyll "$MONTH/$DATE.html" "$MONTH/latest.json" 2>/dev/null || true
if ! git diff --cached --quiet; then
    git commit -m "deploy: $DATE"
    git push origin gh-pages
    echo "Pushed to gh-pages"
else
    echo "No changes to deploy"
fi

# Return to main
git checkout main
rm -rf "$TMP"

echo "=== Done at $(date) ==="
