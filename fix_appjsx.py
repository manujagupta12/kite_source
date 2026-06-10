"""
fix_appjsx.py — removes git merge conflict markers from App.jsx,
keeping the HEAD (<<<<<<< HEAD) version.
"""
import re
from pathlib import Path

target = Path(__file__).parent / "app" / "frontend" / "src" / "App.jsx"

text = target.read_text(encoding="utf-8")

pattern = re.compile(
    r"^<{7}.*?\n"
    r"(.*?)"
    r"^={7}\n"
    r".*?"
    r"^>{7}[^\n]*\n?",
    re.DOTALL | re.MULTILINE
)

original = text
fixed = pattern.sub(r"\1", text)

if fixed == original:
    print("No conflict markers found — file is already clean.")
else:
    target.write_text(fixed, encoding="utf-8")
    conflicts = len(re.findall(r"^<{7}", original, re.MULTILINE))
    print(f"Fixed {conflicts} conflict(s) in {target}")
    print("Kept HEAD version. Vite will hot-reload automatically.")
