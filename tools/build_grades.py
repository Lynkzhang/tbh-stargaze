"""
Build item_grades.json from the TBH guide site's ItemInfoData.csv.

Output: src/resources/item_grades.json
  { "910011": "COMMON", "910051": "COMMON", ... }

Run once when game updates; the web UI will reload it via /grades.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

CSV_PATH = Path(r"D:\opencode\TBH攻略站\extracted\tables\ItemInfoData.csv")
OUT_PATH = Path(__file__).resolve().parent.parent / "src" / "resources" / "item_grades.json"

# Known grades in TBH (from chest.html / rarity.html GRADE_CLS map):
KNOWN_GRADES = {
    "COMMON", "UNCOMMON", "RARE", "LEGENDARY", "IMMORTAL",
    "ARCANA", "BEYOND", "CELESTIAL", "DIVINE", "COSMIC",
}


def main() -> int:
    if not CSV_PATH.exists():
        print(f"ERROR: {CSV_PATH} not found", file=sys.stderr)
        return 1

    # The CSV has a DOUBLE UTF-8 BOM at the start (efbbbf efbbbf). utf-8-sig
    # only strips one BOM. Read raw, strip ALL leading BOMs, then parse.
    raw = CSV_PATH.read_bytes()
    while raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8")

    grades: dict[str, str] = {}
    unknown_grades: set[str] = set()

    import io
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        key = (row.get("ItemKey") or "").strip()
        grade = (row.get("GRADE") or "").strip().upper()
        if not key or not grade:
            continue
        grades[key] = grade
        if grade not in KNOWN_GRADES:
            unknown_grades.add(grade)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(grades, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(grades)} items -> {OUT_PATH}")
    if unknown_grades:
        print(f"WARN: unknown grade values: {sorted(unknown_grades)}")
    # Stats
    from collections import Counter
    counts = Counter(grades.values())
    print("Grade distribution:")
    for g, c in counts.most_common():
        print(f"  {g}: {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
