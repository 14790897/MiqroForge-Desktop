#!/bin/bash
# Verify per-session file isolation.
# Usage: bash scripts/verify-session-isolation.sh
set -euo pipefail

WORKSPACE="${MIQI_WORKSPACE:-$HOME/.forge/workspace}"
PASS=0
FAIL=0

check() {
  local label="$1" result="$2"
  if [ "$result" = "ok" ]; then
    echo "  ✓ $label"
    PASS=$((PASS + 1))
  else
    echo "  ✗ $label — $result"
    FAIL=$((FAIL + 1))
  fi
}

echo "=== Session File Isolation Check ==="
echo "Workspace: $WORKSPACE"
echo ""

# 1. Check no e2e test files in global workspace root
LEAK_COUNT=$(find "$WORKSPACE" -maxdepth 1 -name "e2e_session_file_*" -type f 2>/dev/null | wc -l)
if [ "$LEAK_COUNT" -eq 0 ]; then
  check "Global workspace root has zero e2e test files" ok
else
  check "Global workspace root has zero e2e test files" "found $LEAK_COUNT files: $(find "$WORKSPACE" -maxdepth 1 -name 'e2e_session_file_*' -type f 2>/dev/null | head -3 | tr '\n' ' ')"
fi

# 2. Check files exist under sessions/ subdirectory
SESSION_COUNT=$(find "$WORKSPACE/sessions" -name "e2e_session_file_*" -type f 2>/dev/null | wc -l)
if [ "$SESSION_COUNT" -ge 1 ]; then
  check "e2e test files exist in sessions/ subdirectory" ok
else
  check "e2e test files exist in sessions/ subdirectory" "none found (may be expected if sandbox cleaned)"
fi

# 3. Show each file's location
echo ""
echo "--- File locations ---"
find "$WORKSPACE" -name "e2e_session_file_*" -type f 2>/dev/null | while read f; do
  echo "  $f"
  echo "    content: $(cat "$f" 2>/dev/null | head -c 60)..."
  DIR=$(dirname "$f")
  if echo "$DIR" | grep -q "sessions"; then
    echo "    ✅ in session directory"
  else
    echo "    ❌ NOT in session directory — leak detected!"
    FAIL=$((FAIL + 1))
  fi
done
[ -z "$(find "$WORKSPACE" -name 'e2e_session_file_*' -type f 2>/dev/null)" ] && echo "  (no e2e files found — sandbox has been cleaned)"

# 4. Check directory structure
echo ""
echo "--- Session directory structure ---"
if [ -d "$WORKSPACE/sessions" ]; then
  for d in "$WORKSPACE/sessions"/*/; do
    [ ! -d "$d" ] && continue
    key=$(basename "$d")
    echo "  session: $key"
    if [ -d "${d}files" ]; then
      echo "    ✓ files/ dir exists"
      FC=$(find "${d}files" -type f 2>/dev/null | wc -l)
      echo "    files: $FC"
    else
      echo "    (no files/ — session had no file writes)"
    fi
  done
else
  echo "  (no sessions directory)"
fi

echo ""
echo "=== Result: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] || exit 1
