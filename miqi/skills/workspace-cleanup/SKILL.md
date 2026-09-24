---
name: workspace-cleanup
description: "Organize and clean up the miqi workspace directory (~/.forge/workspace). Classifies generated files into structured subdirectories, archives old files, and produces a cleanup report."
metadata: {"miqi":{"emoji":"🗂️","requires":{"bins":["find","mv","du"]}}}
---

# Workspace Cleanup

Organize `~/.forge/workspace` by classifying generated files into a structured layout, archiving old content, and reporting what was done.

## ⚠️ Sacred directories — NEVER touch these

Before doing anything, confirm these paths are NEVER moved, renamed, or deleted:

```
memory/           ← agent memory (MEMORY.md, LTM_SNAPSHOT.json, LESSONS.jsonl, daily notes)
skills/           ← user-defined skills
sessions/         ← session history
SOUL.md           ← agent identity
IDENTITY.md       ← agent identity
HEARTBEAT.md      ← heartbeat status
TOOLS.md          ← tool descriptions
USER.md           ← user profile
AGENTS.md         ← agent config
```

If unsure whether a file is system-generated, **skip it and report it** rather than moving it.

---

## Target Directory Structure

After cleanup, the workspace should look like this:

```
workspace/
├── memory/                 ← (sacred, untouched)
├── skills/                 ← (sacred, untouched)
├── sessions/               ← (sacred, untouched)
├── SOUL.md                 ← (sacred, untouched)
├── artifacts/
│   ├── papers/             ← PDF papers (already used by paper_download tool)
│   ├── data/               ← scientific data: .cif, .cssr, .xyz, .csv, .json, .dat, .mat
│   ├── reports/            ← generated reports and summaries: .md, .txt, .html
│   ├── scripts/            ← generated scripts: .py, .sh, .bash, .js, .ts
│   ├── downloads/          ← files downloaded from Feishu or external sources
│   └── misc/               ← anything that doesn't fit above
└── archive/
    └── YYYY-MM/            ← files older than 30 days, sorted by month
```

---

## Step-by-Step Procedure

### Step 1 — Survey the workspace

```bash
WORKSPACE="$HOME/.forge/workspace"
echo "=== Current workspace layout ==="
find "$WORKSPACE" -maxdepth 2 \
  -not -path "*/memory*" \
  -not -path "*/skills*" \
  -not -path "*/sessions*" \
  -not -path "*/__pycache__*" \
  | sort

echo ""
echo "=== Total size ==="
du -sh "$WORKSPACE"

echo ""
echo "=== Loose files in workspace root (to be organized) ==="
find "$WORKSPACE" -maxdepth 1 -type f | sort
```

Read the survey output carefully. Build a mental map of what exists before moving anything.

### Step 2 — Create target directories

```bash
WORKSPACE="$HOME/.forge/workspace"
mkdir -p \
  "$WORKSPACE/artifacts/papers" \
  "$WORKSPACE/artifacts/data" \
  "$WORKSPACE/artifacts/reports" \
  "$WORKSPACE/artifacts/scripts" \
  "$WORKSPACE/artifacts/downloads" \
  "$WORKSPACE/artifacts/misc"
```

### Step 3 — Classify and move loose files from workspace root

Work through each loose file (files directly in workspace root, not in subdirectories). Apply these rules in order:

| File pattern | Destination |
|---|---|
| `*.pdf` | `artifacts/papers/` (unless already there) |
| `*.cif`, `*.cssr`, `*.xyz`, `*.mol`, `*.pdb` | `artifacts/data/` |
| `*.csv`, `*.dat`, `*.mat`, `*.npy`, `*.npz`, `*.json` (data outputs) | `artifacts/data/` |
| `*.py`, `*.sh`, `*.bash`, `*.zsh`, `*.js`, `*.ts` | `artifacts/scripts/` |
| `*.md`, `*.txt`, `*.html`, `*.rst` (reports/summaries, not system files) | `artifacts/reports/` |
| Files with names like `output_*`, `result_*`, `report_*`, `summary_*` | `artifacts/reports/` |
| Files with names like `download_*`, `feishu_*`, attachment files | `artifacts/downloads/` |
| Everything else | `artifacts/misc/` |

Move example:
```bash
mv "$WORKSPACE/some_report.md" "$WORKSPACE/artifacts/reports/"
mv "$WORKSPACE/analysis.py" "$WORKSPACE/artifacts/scripts/"
mv "$WORKSPACE/structure.cif" "$WORKSPACE/artifacts/data/"
```

**Do not move files if the destination already has a file with the same name — report the conflict instead.**

### Step 4 — Archive files older than 30 days

Find files in `artifacts/` that haven't been modified in over 30 days and move them to `archive/YYYY-MM/` using their modification date.

```bash
WORKSPACE="$HOME/.forge/workspace"
CUTOFF=30  # days

# Find old files in artifacts (not recursing into archive itself)
find "$WORKSPACE/artifacts" -type f -mtime +$CUTOFF | while read filepath; do
  # Get modification year-month
  YEARMONTH=$(date -r "$filepath" +%Y-%m 2>/dev/null || date +%Y-%m)
  ARCHIVE_DIR="$WORKSPACE/archive/$YEARMONTH"
  mkdir -p "$ARCHIVE_DIR"

  FILENAME=$(basename "$filepath")
  DEST="$ARCHIVE_DIR/$FILENAME"

  # Avoid overwriting: append suffix if name conflicts
  if [ -e "$DEST" ]; then
    DEST="$ARCHIVE_DIR/$(basename "${filepath%.*}")_$(date -r "$filepath" +%s 2>/dev/null || date +%s).${filepath##*.}"
  fi

  mv "$filepath" "$DEST"
  echo "Archived: $filepath → $DEST"
done
```

### Step 5 — Handle subdirectories that are NOT sacred

If there are non-standard subdirectories in the workspace root (not `memory/`, `skills/`, `sessions/`, `artifacts/`, `archive/`):

- If the subdirectory looks like a project working directory (contains code or data), move it into `artifacts/misc/` wholesale.
- If it's empty, remove it: `rmdir "$dir"`
- If unsure, **leave it and report it**.

```bash
# List non-standard subdirectories
find "$WORKSPACE" -maxdepth 1 -mindepth 1 -type d \
  -not -name "memory" \
  -not -name "skills" \
  -not -name "sessions" \
  -not -name "artifacts" \
  -not -name "archive" \
  -not -name "__pycache__"
```

### Step 6 — Cleanup empty directories

```bash
find "$WORKSPACE/artifacts" -type d -empty -delete 2>/dev/null
find "$WORKSPACE/archive" -type d -empty -delete 2>/dev/null
```

### Step 7 — Generate cleanup report

After all moves are done, produce a concise summary:

```
=== Workspace Cleanup Report — YYYY-MM-DD ===

Workspace: ~/.forge/workspace
Total size after cleanup: X MB

Files organized:
  papers/    : N files
  data/      : N files
  reports/   : N files
  scripts/   : N files
  downloads/ : N files
  misc/      : N files

Files archived (>30 days old): N files → archive/YYYY-MM/

Conflicts (not moved, need manual review):
  - filename.ext (reason)

Skipped / uncertain:
  - directory_name/ (reason: could not determine purpose)

Sacred directories (untouched):
  memory/, skills/, sessions/, SOUL.md, ...
```

Send this report to the user (via the channel that triggered the task, e.g. Feishu message or console output).

---

## Running as a Scheduled Task

To run cleanup automatically every Sunday at 22:00 (Asia/Shanghai):

```
cron(
  action="add",
  message="Run workspace-cleanup skill: organize and archive workspace files, then report results",
  cron_expr="0 22 * * 0",
  tz="Asia/Shanghai"
)
```

To run immediately (one-time):
```
Run the workspace-cleanup skill now.
```

---

## Safety Rules Summary

1. **Always survey before acting** — never move files blindly.
2. **Sacred list is absolute** — `memory/`, `skills/`, `sessions/`, and system `.md` files are never touched.
3. **Conflict = skip + report** — if a destination file already exists, do NOT overwrite; report the conflict.
4. **Unknown subdirectory = leave + report** — don't guess the purpose of unfamiliar directories.
5. **No deletion** — files are only moved (to `artifacts/` or `archive/`), never deleted. If the user wants to purge old archives, that's a separate explicit request.
