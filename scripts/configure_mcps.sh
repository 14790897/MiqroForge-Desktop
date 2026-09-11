#!/usr/bin/env bash
# configure_mcps.sh — Register all bundled MCP servers into your miqi config.
#
# This script calls `miqi config mcp add` for each bundled MCP so you
# do not have to edit config.json by hand.  Credentials (API keys etc.) are
# purposely NOT written here — see the prompts at the end.
#
# LLM-backed MCP servers (pdf2zh and any future ones) automatically follow
# the model configured in miqi defaults. To switch models do NOT edit pdf2zh
# directly — instead run:
#
#     miqi config defaults --model <new-model>
#     bash scripts/configure_mcps.sh    # or: miqi config sync-llm
#
# Prerequisites:
#   1. miqi is installed (uv sync or pip install -e .) and `miqi onboard` has
#      been run so a default provider + model exist.
#   2. scripts/setup_mcps.sh has already been run (venvs exist).
#
# Usage:
#   bash scripts/configure_mcps.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MCPS_DIR="$REPO_ROOT/mcps"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[configure_mcps]${NC} $*"; }
warn()  { echo -e "${YELLOW}[configure_mcps] WARN:${NC} $*"; }
error() { echo -e "${RED}[configure_mcps] ERROR:${NC} $*" >&2; exit 1; }

STEP=0
step() { STEP=$((STEP+1)); echo -e "\n${GREEN}[configure_mcps]${NC} ── Step ${STEP}: $* ──"; }

# ──────────────────────────────────────────────────────────────────────────────
# Pre-flight
# ──────────────────────────────────────────────────────────────────────────────

echo ""
info "MiQroForge MCP configure — registering 7 MCP servers into miqi config"

step "Pre-flight: verify miqi is installed and submodules are set up"
command -v miqi &>/dev/null || error \
    "miqi is not in PATH. Install it first: pip install -e ."
info "  ✓ $(miqi --version 2>/dev/null | head -1)"

for dir in zeopp-backend raspa-mcp mofstructure-mcp mofchecker-mcp pdftranslate-mcp feishu-mcp miqrophi-mcp; do
    if [[ ! -f "$MCPS_DIR/$dir/pyproject.toml" ]]; then
        warn "mcps/$dir has no pyproject.toml — submodule not checked out. Run: bash scripts/setup_mcps.sh --init"
        continue
    fi
    [[ -d "$MCPS_DIR/$dir/.venv" || -f "$MCPS_DIR/$dir/uv.lock" ]] || {
        warn "mcps/$dir does not appear set up — run scripts/setup_mcps.sh first."
    }
done

# ──────────────────────────────────────────────────────────────────────────────
# Resolve per-MCP Python interpreters
# ──────────────────────────────────────────────────────────────────────────────

step "Resolve per-MCP interpreter paths"
ZEOPP_CMD="$(command -v uv)"
RASPA2_CMD="$(command -v uv)"
MOFSTRUCTURE_CMD="$MCPS_DIR/mofstructure-mcp/.venv/bin/python"
MOFCHECKER_CMD="$MCPS_DIR/mofchecker-mcp/.venv/bin/python"
PDF2ZH_CMD="$MCPS_DIR/pdftranslate-mcp/.venv/bin/python"
FEISHU_CMD="$MCPS_DIR/feishu-mcp/.venv/bin/python"
MIQROPHI_CMD="$MCPS_DIR/miqrophi-mcp/.venv/bin/python"
info "  zeopp / raspa2 → uv (project-local run)"
info "  mofstructure   → $MOFSTRUCTURE_CMD"
info "  mofchecker     → $MOFCHECKER_CMD"
info "  pdf2zh         → $PDF2ZH_CMD"
info "  feishu-mcp     → $FEISHU_CMD"
info "  miqrophi       → $MIQROPHI_CMD"

# ──────────────────────────────────────────────────────────────────────────────
# Register each server (non-interactive, no credentials)
# ──────────────────────────────────────────────────────────────────────────────

step "Register MCP servers with miqi config (5 non-interactive servers)"

register() {
    local label="$1"
    shift
    info "Registering $label..."
    miqi config mcp add "$@"
    info "  ✓ $label registered"
}

# zeopp-backend — no credentials needed, long-running analysis
register "zeopp" zeopp \
    --command "$ZEOPP_CMD" \
    --arg run \
    --arg --project \
    --arg "$MCPS_DIR/zeopp-backend" \
    --arg python \
    --arg -m \
    --arg app.mcp.stdio_main \
    --timeout 600 \
    --lazy \
    --description "Zeo++ porous material geometry analysis: accessible volume, pore size, channel detection"

# raspa-mcp — RASPA2 simulation can run for several hours
register "raspa2" raspa2 \
    --command "$RASPA2_CMD" \
    --arg run \
    --arg --directory \
    --arg "$MCPS_DIR/raspa-mcp" \
    --arg raspa-mcp \
    --timeout 21600 \
    --lazy \
    --description "RASPA2 molecular simulation: build input files, parse outputs, GCMC/MD workflows"

# mofstructure-mcp — MOF structural analysis
register "mofstructure" mofstructure \
    --command "$MOFSTRUCTURE_CMD" \
    --arg -m \
    --arg mofstructure.mcp_server \
    --timeout 600 \
    --lazy \
    --description "MOF structural analysis: identify building blocks, topology, metal nodes, linkers"

# mofchecker-mcp — MOF structure validation (Python 3.10 venv)
register "mofchecker" mofchecker \
    --command "$MOFCHECKER_CMD" \
    --arg -m \
    --arg mofchecker.mcp_server \
    --timeout 120 \
    --lazy \
    --description "MOF structure checker: validate CIF files, detect common defects, check geometry"

# miqrophi-mcp — epitaxial lattice matching, no credentials needed
register "miqrophi" miqrophi \
    --command "$MIQROPHI_CMD" \
    --arg -m \
    --arg miqrophi.mcp_server \
    --timeout 600 \
    --lazy \
    --description "Epitaxial lattice matching: CIF surface analysis, substrate screening, strain calculation"

# ──────────────────────────────────────────────────────────────────────────────
# Feishu — prompt for credentials, then configure channel + MCP in one shot
# ──────────────────────────────────────────────────────────────────────────────

step "Configure Feishu channel and feishu-mcp (interactive)"
info "Configuring Feishu channel and feishu-mcp..."
read -rp "  Feishu App ID (cli_xxx, leave blank to skip): " FEISHU_APP_ID
if [[ -n "$FEISHU_APP_ID" ]]; then
    read -rsp "  Feishu App Secret: " FEISHU_APP_SECRET
    echo ""
    miqi config feishu \
        --app-id "$FEISHU_APP_ID" \
        --app-secret "$FEISHU_APP_SECRET" \
        --mcp-python "$FEISHU_CMD"
else
    warn "Skipping Feishu — run 'miqi config feishu' later."
fi

# ──────────────────────────────────────────────────────────────────────────────
# pdf2zh — auto-fills LLM credentials already configured in miqi
# ──────────────────────────────────────────────────────────────────────────────

step "Configure pdf2zh MCP (reads LLM provider credentials from miqi config)"
info "Configuring pdf2zh MCP (auto-reads provider credentials, no prompts)..."
# --no-prompt forces the command to use the miqi default provider+model verbatim
# and refuse to ask interactively. This is the only path that guarantees pdf2zh
# stays in lock-step with `miqi config defaults`.
if ! miqi config pdf2zh --mcp-python "$PDF2ZH_CMD" --timeout 3600 --no-prompt; then
    warn "pdf2zh auto-config failed. Make sure 'miqi onboard' set up a default provider first."
fi

# ──────────────────────────────────────────────────────────────────────────────
# Final sync: realign every LLM-backed MCP with the current miqi default model.
# This is what makes the workflow truly idempotent — re-run this script after
# changing the main model and every MCP picks it up.
# ──────────────────────────────────────────────────────────────────────────────

step "Sync all LLM-backed MCP servers with miqi defaults"
if ! miqi config sync-llm; then
    warn "sync-llm failed. You can re-run it later: miqi config sync-llm"
fi

echo ""
info "All MCP servers configured."
echo ""
echo "  💡 To change the LLM used by pdf2zh (and any future LLM-backed MCP):"
echo "       miqi config defaults --model <new-model>"
echo "       miqi config sync-llm"
echo ""
