"""
Extract Steam Market name mappings from tbh-copilot project.

Reads:
  tbh-copilot/engine/gearnames.js       - 5760 gear items (ItemKey -> English name)
  tbh-copilot/engine/materialfx.js      - 79 craft materials  (ItemKey -> English name)
  tbh-copilot/engine/itemnames.js       - ~300 other items     (ItemKey -> {locale: name})
                                          covers things like Kingdom 50th Anniversary Coin,
                                          quest currencies, soul stones, etc.

Writes to box-queue-reader/src/resources/:
  gear_market_names.json      - {ItemKey: "English"} for gear (suffix: " (Grade)")
  material_market_names.json  - {ItemKey: "English"} for materials (no suffix)
  generic_market_names.json   - {ItemKey: "English"} for everything else (no suffix)

Why three files: gear needs " (Grade)" appended to the hash, others don't.
Lookup priority: gear -> material -> generic.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SOURCE_DIR = Path(r"C:\Users\ASUS\AppData\Local\Temp\opencode\tbh-copilot\engine")
DEST_DIR = Path(__file__).resolve().parent.parent / "src" / "resources"


def extract_gear_names() -> dict[str, str]:
    """gearnames.js: ;(function(x){x.TBH_GEARNAMES={...};})(self||window);"""
    raw = (SOURCE_DIR / "gearnames.js").read_text(encoding="utf-8")
    match = re.search(r"TBH_GEARNAMES\s*=\s*(\{.*?\});", raw, re.DOTALL)
    if not match:
        print("ERROR: TBH_GEARNAMES dict not found in gearnames.js", file=sys.stderr)
        sys.exit(1)
    return json.loads(match.group(1))


def extract_material_names() -> dict[str, str]:
    """materialfx.js: ;(function(x){x.TBH_MATFX=[{key,name,...},...];})(...)"""
    raw = (SOURCE_DIR / "materialfx.js").read_text(encoding="utf-8")
    match = re.search(r"TBH_MATFX\s*=\s*(\[.*?\]);", raw, re.DOTALL)
    if not match:
        print("ERROR: TBH_MATFX array not found in materialfx.js", file=sys.stderr)
        sys.exit(1)
    data = json.loads(match.group(1))
    return {str(e["key"]): e["name"] for e in data if e.get("key") and e.get("name")}


def extract_generic_names() -> dict[str, str]:
    """itemnames.js: ;(function(g){g.TBH_ITEMNAMES={ID:{en-US:..., ...}, ...};})(...)"""
    raw = (SOURCE_DIR / "itemnames.js").read_text(encoding="utf-8")
    match = re.search(r"TBH_ITEMNAMES\s*=\s*(\{.*?\});", raw, re.DOTALL)
    if not match:
        print("ERROR: TBH_ITEMNAMES dict not found in itemnames.js", file=sys.stderr)
        sys.exit(1)
    data = json.loads(match.group(1))
    out: dict[str, str] = {}
    for key, locs in data.items():
        if isinstance(locs, dict):
            en = locs.get("en-US")
            if en:
                out[str(key)] = en
    return out


def main() -> int:
    DEST_DIR.mkdir(parents=True, exist_ok=True)

    print("Extracting gear names...")
    gears = extract_gear_names()
    (DEST_DIR / "gear_market_names.json").write_text(
        json.dumps(gears, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  Wrote {len(gears)} gear")

    print("Extracting material (craft) names...")
    mats = extract_material_names()
    (DEST_DIR / "material_market_names.json").write_text(
        json.dumps(mats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  Wrote {len(mats)} crafting materials")

    print("Extracting generic item names (currencies, quest items, etc.)...")
    generics = extract_generic_names()
    # De-dupe: drop entries already covered by gear or material so the file
    # stays small and the lookup priority (gear -> material -> generic) is honoured.
    deduped = {k: v for k, v in generics.items() if k not in gears and k not in mats}
    (DEST_DIR / "generic_market_names.json").write_text(
        json.dumps(deduped, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  Wrote {len(deduped)} generic items (after dedupe from {len(generics)})")

    # Spot-check the ones we care about
    print("\n=== Spot check ===")
    for k in ["521171", "524171", "527171", "110001", "144002", "190004", "160005", "160006"]:
        n = gears.get(k) or mats.get(k) or deduped.get(k) or "(none)"
        source = (
            "gear" if k in gears
            else "material" if k in mats
            else "generic" if k in deduped
            else "—"
        )
        print(f"  {k}: {n}   [{source}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
