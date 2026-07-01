import json, sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

with open(r'D:\opencode\TBH攻略站\box-queue-reader\dump\toc.json', 'r', encoding='utf-8') as f:
    toc = json.load(f)

interesting = []
for e in toc['entries']:
    name = e['name']
    if name.startswith('_tcl_data') or name.startswith('tcl') or 'tzdata' in name:
        continue
    if name.endswith('.enc') or name.endswith('.msg'):
        continue
    interesting.append(e)

business = [e for e in interesting if any(k in e['name'].lower() for k in
    ['agent', 'item.json', 'watched', 'color', 'drop', 'tbh', '.js', '.json'])]

print('=== Business resources ===')
for e in business:
    print(f"  [{e['type']}] {e['name']}  cmpr={e['cmprLen']:,} uncmpr={e['uncmprLen']:,}")

print('\n=== Non-stdlib type s/o/z + frida ===')
for e in interesting:
    if e['type'] in ('s', 'o', 'z') or 'frida' in e['name'].lower():
        print(f"  [{e['type']}] {e['name']}  cmpr={e['cmprLen']:,} uncmpr={e['uncmprLen']:,}")

print('\n=== ALL non-stdlib entries ===')
for e in interesting:
    print(f"  [{e['type']}] {e['name']}  cmpr={e['cmprLen']:,} uncmpr={e['uncmprLen']:,}")
