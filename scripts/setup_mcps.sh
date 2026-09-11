#!/usr/bin/env bash
# setup_mcps.sh — Install Python venvs for all bundled MCP submodules.
#
# Requirements:
#   - uv (https://docs.astral.sh/uv/getting-started/installation/)
#   - git submodules already initialised (run with --init if not)
#
# Usage:
#   bash scripts/setup_mcps.sh [--init]
#
#   --init   Also initialise / update git submodules before installing.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MCPS_DIR="$REPO_ROOT/mcps"

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'  # no colour

info()    { echo -e "${GREEN}[setup_mcps]${NC} $*"; }
warn()    { echo -e "${YELLOW}[setup_mcps] WARN:${NC} $*"; }
error()   { echo -e "${RED}[setup_mcps] ERROR:${NC} $*" >&2; exit 1; }

check_cmd() {
    command -v "$1" &>/dev/null || error "'$1' is not installed. $2"
}

STEP=0
step() { STEP=$((STEP+1)); echo -e "\n${GREEN}[setup_mcps]${NC} ── Step ${STEP}: $* ──"; }

# ──────────────────────────────────────────────────────────────────────────────
# Pre-flight checks
# ──────────────────────────────────────────────────────────────────────────────

echo ""
info "MiQroForge MCP setup — 7 submodules:"
info "  zeopp-backend · raspa-mcp · mofstructure-mcp · mofchecker-mcp"
info "  pdftranslate-mcp · feishu-mcp · miqrophi-mcp"

step "Pre-flight: checking required tools (uv, git)"
check_cmd uv "Install via: curl -LsSf https://astral.sh/uv/install.sh | sh"
check_cmd git "Install git first."
info "  ✓ uv $(uv --version 2>/dev/null | head -1)"
info "  ✓ git $(git --version)"

# Optional: initialise / update submodules
if [[ "${1:-}" == "--init" ]]; then
    step "Initialise git submodules (--force, --recursive)"
    info "  Cloning / checking out all mcps/* submodules from upstream..."
    git -C "$REPO_ROOT" submodule update --init --force --recursive
    info "  ✓ All submodules checked out"
fi

step "Verify submodule contents (pyproject.toml / uv.lock)"
info "  Checking each mcps/* directory is fully checked out..."
# Verify submodule directories contain project files (not just git placeholders)
for dir in zeopp-backend raspa-mcp mofstructure-mcp mofchecker-mcp pdftranslate-mcp feishu-mcp miqrophi-mcp; do
    [[ -f "$MCPS_DIR/$dir/pyproject.toml" || -f "$MCPS_DIR/$dir/uv.lock" ]] || error \
        "mcps/$dir has no pyproject.toml — submodule not checked out. Run: bash scripts/setup_mcps.sh --init"
    info "  ✓ mcps/$dir"
done

# ──────────────────────────────────────────────────────────────────────────────
# Helper: create venv + install package
# ──────────────────────────────────────────────────────────────────────────────

setup_venv() {
    local name="$1"
    local python_ver="$2"   # e.g. "3.10", "3.12", or "" to let uv choose
    local dir="$MCPS_DIR/$name"

    info "Setting up $name (Python ${python_ver:-auto})..."
    [[ -f "$dir/pyproject.toml" || -f "$dir/setup.py" ]] || error \
        "mcps/$name has no pyproject.toml — submodule not checked out. Run: bash scripts/setup_mcps.sh --init"
    pushd "$dir" > /dev/null

    info "  → Creating virtual environment (.venv)..."
    if [[ -n "$python_ver" ]]; then
        uv venv .venv --python "$python_ver"
    else
        uv venv .venv
    fi

    info "  → Installing package in editable mode (uv pip install -e .)..."
    # Install package in editable mode into the new venv
    uv pip install --python .venv/bin/python -e . --quiet

    popd > /dev/null
    info "  ✓ $name"
}

# ──────────────────────────────────────────────────────────────────────────────
# Helper: uv sync (for projects whose pyproject.toml drives everything)
# ──────────────────────────────────────────────────────────────────────────────

setup_uv_sync() {
    local name="$1"
    local dir="$MCPS_DIR/$name"

    info "Setting up $name (uv sync)..."
    [[ -f "$dir/pyproject.toml" || -f "$dir/uv.lock" ]] || error \
        "mcps/$name has no pyproject.toml — submodule not checked out. Run: bash scripts/setup_mcps.sh --init"
    pushd "$dir" > /dev/null
    info "  → Syncing and installing all dependencies (uv sync)..."
    uv sync --quiet
    popd > /dev/null
    info "  ✓ $name"
}

# ──────────────────────────────────────────────────────────────────────────────
# Per-MCP setup
# ──────────────────────────────────────────────────────────────────────────────

step "Install Python environments for all 7 MCPs"
info "  First run may take several minutes per MCP (downloading packages)."

# zeopp-backend — Python 3.10+, uses uv natively
setup_uv_sync "zeopp-backend"

# raspa-mcp — Python 3.11+, uses uv natively
setup_uv_sync "raspa-mcp"
warn "raspa-mcp: The RASPA2 simulation engine must be compiled separately."
warn "  After this script finishes, run once: cd mcps/raspa-mcp && uv run raspa-mcp-setup"

# mofstructure-mcp — Python 3.9+
setup_venv "mofstructure-mcp" "3.12"

# mofchecker-mcp — REQUIRES Python < 3.11 (pyeqeq / pybind11 constraint)
setup_venv "mofchecker-mcp" "3.10"

# pdftranslate-mcp — Python 3.10–3.12 (pdf2zh does NOT support 3.13+)
setup_venv "pdftranslate-mcp" "3.12"

# feishu-mcp — Python 3.11+
setup_venv "feishu-mcp" "3.12"

# miqrophi-mcp — Python 3.10+ (epitaxial lattice matching), install with MCP extras
info "Setting up miqrophi-mcp (Python 3.12)..."
[[ -f "$MCPS_DIR/miqrophi-mcp/pyproject.toml" || -f "$MCPS_DIR/miqrophi-mcp/setup.py" ]] || error \
    "mcps/miqrophi-mcp has no pyproject.toml — submodule not checked out. Run: bash scripts/setup_mcps.sh --init"
pushd "$MCPS_DIR/miqrophi-mcp" > /dev/null
info "  → Creating virtual environment (.venv)..."
uv venv .venv --python 3.12
info "  → Installing package with MCP extras (uv pip install -e '.[mcp]')..."
uv pip install --python .venv/bin/python -e ".[mcp]" --quiet
popd > /dev/null
info "  ✓ miqrophi-mcp"

# ──────────────────────────────────────────────────────────────────────────────
# Done
# ──────────────────────────────────────────────────────────────────────────────

echo ""
info "All MCP venvs installed successfully."
echo ""
echo "  ⚠️  RASPA2 engine still needs one-time compilation:"
echo "       cd mcps/raspa-mcp && uv run raspa-mcp-setup"
echo ""
echo "  Next step: register MCPs with miqi:"
echo "    bash scripts/configure_mcps.sh"
echo ""
echo "  Or see docs/mcp-integration.md for manual configuration."
