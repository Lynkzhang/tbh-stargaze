"""Extract business resources from PyInstaller-packed exe."""
import json
import os
import zlib

EXE_PATH = r'E:\tbhmod\xmodhub\tools\d67e372c6f9f9aab1018fc35b333d06a.exe'
TOC_PATH = r'D:\opencode\TBH攻略站\box-queue-reader\dump\toc.json'
OUT_DIR  = r'D:\opencode\TBH攻略站\box-queue-reader\dump\resources'

# Targeted files - business logic + configs (NOT 3rd party libs)
TARGETS = {
    'drop_items_agent.js',
    'item.json',
    'item_color.json',
    'watched_ids.json',
}

def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(TOC_PATH, 'r', encoding='utf-8') as f:
        toc = json.load(f)
    pkg_start = toc['pkg_start']
    with open(EXE_PATH, 'rb') as f:
        data = f.read()
    for e in toc['entries']:
        if e['name'] not in TARGETS:
            continue
        blob = data[pkg_start + e['pos'] : pkg_start + e['pos'] + e['cmprLen']]
        if e['compFlag']:
            blob = zlib.decompress(blob)
        out_path = os.path.join(OUT_DIR, e['name'])
        with open(out_path, 'wb') as g:
            g.write(blob)
        print(f"Extracted: {e['name']:30} -> {len(blob):>8,} bytes")

if __name__ == '__main__':
    main()
