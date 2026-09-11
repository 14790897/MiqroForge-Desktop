"""Verify generated PDF is valid and contains expected content."""
import glob as _glob
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')

workspace = sys.argv[1]
expected_name = sys.argv[2] if len(sys.argv) > 2 else None

files = _glob.glob(os.path.join(workspace, "**", "*.pdf"), recursive=True)
if expected_name:
    files = [f for f in files if os.path.basename(f) == expected_name]
if not files:
    detail = f"{expected_name} not found" if expected_name else "no pdf found"
    json.dump({"pass": False, "checks": [{"label": "find pdf", "pass": False, "detail": detail}]}, sys.stdout, ensure_ascii=False)
    sys.exit(1)

filepath = max(files, key=os.path.getmtime)
size = os.path.getsize(filepath)

result = {"pass": True, "checks": [], "file": os.path.basename(filepath), "size": size}

def check(label, condition, detail=""):
    result["checks"].append({"label": label, "pass": bool(condition), "detail": detail})
    if not condition:
        result["pass"] = False

# Check file exists and has content
check("file exists", os.path.exists(filepath), filepath)
check("file size > 500 bytes", size > 500, f"got {size} bytes")

# Check PDF header
with open(filepath, "rb") as f:
    head = f.read(5)
check("valid PDF header", head == b"%PDF-", f"got {head!r}")

# Check PDF has reasonable number of pages by counting /Type /Page tokens
# (using word boundary to avoid matching /Type /Pages which is the page tree node)
with open(filepath, "rb") as f:
    content = f.read()
page_markers = len(re.findall(rb"/Type\s*/Page\b", content))
check("at least 1 page", page_markers >= 1, f"got {page_markers} page markers")

json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
sys.exit(0 if result["pass"] else 1)
