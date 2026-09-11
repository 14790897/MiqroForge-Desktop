#!/usr/bin/env bash
# MiQroForge Environment Diagnostic Script
# Run: bash diagnose.sh  or  curl -sSL https://... | bash
# Output: paste into GitHub Issue "环境" section

set -euo pipefail

echo "--- MiQroForge DIAGNOSTIC ---"
echo ""

# ---- OS ----
echo "**OS**"
uname -a 2>/dev/null || echo "N/A"
if [ -f /etc/os-release ]; then
    grep PRETTY_NAME /etc/os-release 2>/dev/null | cut -d'"' -f2 || true
fi
echo ""

# ---- Python ----
echo "**Python**"
python3 --version 2>/dev/null || python --version 2>/dev/null || echo "NOT FOUND"
which python3 2>/dev/null || which python 2>/dev/null || echo "(path unknown)"
echo ""

# ---- MiQroForge ----
echo "**MiQroForge**"
if python3 -c "import miqi" >/dev/null 2>&1; then
    python3 -c "import miqi; print('version:', miqi.__version__)"
elif python -c "import miqi" >/dev/null 2>&1; then
    python -c "import miqi; print('version:', miqi.__version__)"
else
    echo "NOT INSTALLED"
fi
echo ""

# ---- pip key deps ----
echo "**pip (key deps)**"
python3 -m pip show miqi pydantic httpx loguru 2>/dev/null \
    || python -m pip show miqi pydantic httpx loguru 2>/dev/null \
    || echo "(pip not available)"
echo ""

# ---- Node ----
echo "**Node.js**"
node --version 2>/dev/null || echo "NOT FOUND"
npm --version 2>/dev/null || echo "(npm not found)"
echo ""

# ---- WSL (if on Windows) ----
echo "**WSL**"
wsl.exe --list --verbose 2>/dev/null | tr -d '\0\015' || echo "NOT AVAILABLE (no WSL or not Windows)"
echo ""

# ---- bwrap ----
echo "**bwrap**"
FOUND=false
if which bwrap >/dev/null 2>&1; then
    bwrap --version 2>/dev/null && FOUND=true
elif wsl.exe -d AIShadowSandbox -- which bwrap >/dev/null 2>&1; then
    echo "(AIShadowSandbox)"
    wsl.exe -d AIShadowSandbox -- bwrap --version 2>/dev/null | tr -d '\0\015'
    FOUND=true
elif wsl.exe -d Ubuntu -- which bwrap >/dev/null 2>&1; then
    echo "(Ubuntu)"
    wsl.exe -d Ubuntu -- bwrap --version 2>/dev/null | tr -d '\0\015'
    FOUND=true
elif wsl.exe -- which bwrap >/dev/null 2>&1; then
    echo "(default WSL)"
    wsl.exe -- bwrap --version 2>/dev/null | tr -d '\0\015'
    FOUND=true
fi
$FOUND || echo "NOT FOUND"
echo ""

# ---- Sandbox State ----
echo "**Sandbox State**"
if [ -f ~/.miqi/sandbox_state.json ]; then
    cat ~/.miqi/sandbox_state.json 2>/dev/null || echo "(read failed)"
else
    echo "NO STATE FILE"
fi
echo ""

# ---- Disk ----
echo "**Disk**"
df -h / 2>/dev/null || df -h . 2>/dev/null || echo "(df not available)"
echo ""

# ---- Git ----
echo "**Git**"
git log --oneline -1 2>/dev/null || echo "(not a git repo or git not found)"
echo ""

echo "--- END DIAGNOSTIC ---"
