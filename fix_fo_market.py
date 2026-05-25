"""
fix_fo_market.py  —  run from repo root: python fix_fo_market.py
Adds market="FO" to every signal that _mock_signal() and _xls_signal() produce.
Works on ANY version of main.py by finding function boundaries, not exact strings.
"""
import ast, sys, re
from pathlib import Path

TARGET = Path("app/backend/main.py")
if not TARGET.exists():
    sys.exit("ERROR: run from repo root (where app/ folder is)")

src = TARGET.read_text(encoding="utf-8")

# ── Diagnostic: show what we actually find ───────────────────
print("  Scanning", TARGET)
mock_has = '"market": "FO"' in src or '"market":"FO"' in src
print(f"  market=FO already present: {mock_has}")

if mock_has:
    print("  Already patched — nothing to do.")
    sys.exit(0)

# ── Strategy: find `def _mock_signal():` and insert after `return {` ─
# We inject  "market": "FO",  as the very first key in the return dict.

def inject_after(src, func_name, insert_line):
    """
    Find `return {` inside func_name and insert insert_line right after it.
    """
    lines = src.splitlines(keepends=True)
    in_func = False
    depth = 0
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # detect function start
        if re.match(rf'^def {func_name}\(', stripped):
            in_func = True
            depth = 0

        if in_func:
            # detect indented return {
            if stripped == 'return {' and depth == 0:
                result.append(line)
                result.append(insert_line)
                i += 1
                continue
            # also handle  return {\n  or return {   "key":
            if stripped.startswith('return {') and depth == 0:
                result.append(line)
                result.append(insert_line)
                i += 1
                continue

        result.append(line)
        i += 1
    return "".join(result)

# Detect indentation used in _mock_signal return dict
# Look for the line after `return {` in _mock_signal
mock_indent = "        "  # default 8 spaces
m = re.search(r'def _mock_signal\(\):.*?return \{(.*?)\}', src, re.DOTALL)
if m:
    first_key_line = m.group(1).split('\n')[1] if '\n' in m.group(1) else ""
    ind_match = re.match(r'^(\s+)', first_key_line)
    if ind_match:
        mock_indent = ind_match.group(1)

xls_indent = "            "  # default 12 spaces
m2 = re.search(r'def _xls_signal\(\):.*?sig = \{(.*?)\}', src, re.DOTALL)
if m2:
    first_key_line2 = m2.group(1).split('\n')[1] if '\n' in m2.group(1) else ""
    ind_match2 = re.match(r'^(\s+)', first_key_line2)
    if ind_match2:
        xls_indent = ind_match2.group(1)

# ── Inject into _mock_signal ─────────────────────────────────
MOCK_INSERT = f'{mock_indent}"market": "FO",\n'

# Find `def _mock_signal():` and its `return {`
lines = src.splitlines(keepends=True)
new_lines = []
in_mock = False
mock_done = False
for line in lines:
    stripped = line.strip()
    if re.match(r'^def _mock_signal\(\):', stripped):
        in_mock = True
    if in_mock and not mock_done and stripped == 'return {':
        new_lines.append(line)
        new_lines.append(MOCK_INSERT)
        mock_done = True
        continue
    new_lines.append(line)
src = "".join(new_lines)

# ── Inject into _xls_signal ──────────────────────────────────
XLS_INSERT = f'{xls_indent}"market": "FO",\n'

lines = src.splitlines(keepends=True)
new_lines = []
in_xls = False
xls_done = False
for line in lines:
    stripped = line.strip()
    if re.match(r'^def _xls_signal\(\):', stripped):
        in_xls = True
    if in_xls and not xls_done and stripped == 'sig = {':
        new_lines.append(line)
        new_lines.append(XLS_INSERT)
        xls_done = True
        continue
    new_lines.append(line)
src = "".join(new_lines)

# ── Verify and save ──────────────────────────────────────────
if mock_done:
    print("  [OK] _mock_signal() — market=FO injected")
else:
    print("  [WARN] _mock_signal() — could not inject (return { not found)")

if xls_done:
    print("  [OK] _xls_signal()  — market=FO injected")
else:
    print("  [WARN] _xls_signal() — could not inject (sig = { not found)")

if mock_done or xls_done:
    TARGET.write_text(src, encoding="utf-8")
    print(f"\n  Saved {TARGET}")
    print("  Restart backend: cd app/backend && python main.py")
else:
    print("\n  No changes made. Check main.py manually.")
