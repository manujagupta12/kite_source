"""
fix_conflict.py — removes git merge conflict markers from main.py,
keeping the HEAD (<<<<<<< HEAD) version.
Run once: python fix_conflict.py
"""
import re, sys
from pathlib import Path

target = Path(__file__).parent / "app" / "backend" / "main.py"

text = target.read_text(encoding="utf-8")

# Pattern: <<<<<<< HEAD\n...\n=======\n...\n>>>>>>> hash
# Keep only the HEAD side
def resolve_conflicts(src: str) -> str:
    pattern = re.compile(
        r"^<{7}.*?\n"           # <<<<<<< HEAD
        r"(.*?)"                 # HEAD content (keep this)
        r"^={7}\n"               # =======
        r".*?"                   # remote content (discard)
        r"^>{7}[^\n]*\n?",       # >>>>>>> hash
        re.DOTALL | re.MULTILINE
    )
    resolved = pattern.sub(r"\1", src)
    return resolved

original = text
fixed = resolve_conflicts(text)

if fixed == original:
    print("No conflict markers found — file is already clean.")
else:
    target.write_text(fixed, encoding="utf-8")
    conflicts = len(re.findall(r"^<{7}", original, re.MULTILINE))
    print(f"Fixed {conflicts} conflict(s) in {target}")
    print("Kept HEAD version. Restart the backend now.")
