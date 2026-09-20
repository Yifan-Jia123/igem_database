"""
[已被 build_all_nodes.py 取代 —— 除非明确知道在做什么, 不要运行本脚本]

Fetch InChI Keys for all compounds in all_nodes.tsv using PubChem PUG REST API.
Also fetches InChI and SMILES for completeness.

## 为什么不接入流程 (2026-09-17 核实)

1. **它是死代码**: run_all.py 与 run_workflow.py 都不调用它。
2. **功能已由 build_all_nodes.py 内联实现**: 该脚本自己就查 PubChem 拿 InChI Key,
   且产出的正是仓库现表 `for_graph/all_nodes.tsv` 的 3 列格式 (ChEBI ID / Name / InChI Key)。
3. **跑它会损坏表**: 本脚本产出 **6 列** (多出 InChI / SMILES / PubChem CID),
   且默认原地写回 `for_graph/all_nodes.tsv` —— 直接用 6 列格式覆盖掉现表的 3 列,
   还会绕过 update_database.py 的备份/回滚机制 (它是 for_* 的第二个写入者)。

所以默认路径已改空: 必须显式给 --input= 和 --output= 才会运行, 防止误跑写坏 for_*。
要重建 all_nodes 请用: python run_all.py --merge
"""
import csv
import requests
import time
import json
import os
import sys

# 默认留空 —— 见上方 docstring。原先这里硬编码 for_graph/all_nodes.tsv 作为
# 输入**和**输出, 一次误跑就不可逆地覆盖现表。
INPUT = None
OUTPUT = None
CACHE_FILE = None
for _a in sys.argv[1:]:
    if _a.startswith('--input='):
        INPUT = os.path.abspath(_a[len('--input='):])
    elif _a.startswith('--output='):
        OUTPUT = os.path.abspath(_a[len('--output='):])
    elif _a.startswith('--cache='):
        CACHE_FILE = os.path.abspath(_a[len('--cache='):])

if not INPUT or not OUTPUT:
    sys.exit(__doc__.strip() +
             '\n\n本脚本不接入流程 (见上方说明)。确需运行请显式指定:\n'
             '  python fetch_inchikey.py --input=<all_nodes.tsv> --output=<新文件>\n'
             '重建 all_nodes 的正确方式是: python run_all.py --merge')
if CACHE_FILE is None:
    CACHE_FILE = os.path.join(os.path.dirname(OUTPUT), '_inchikey_cache.json')
BATCH_INTERVAL = 0.25  # PubChem rate limit: ~4-5/sec

# ---- Step 1: read compounds ----
compounds = []
with open(INPUT, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        compounds.append(row)

print(f'Total compounds: {len(compounds)}')

# ---- Step 2: load cache ----
cache = {}
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, 'r', encoding='utf-8') as f:
        cache = json.load(f)
    print(f'Loaded {len(cache)} cached entries')

# ---- Step 3: fetch from PubChem ----
to_fetch = [c for c in compounds if c['ChEBI ID'] not in cache]

if to_fetch:
    print(f'Fetching {len(to_fetch)} compounds...')
    for idx, comp in enumerate(to_fetch):
        chebi_id = comp['ChEBI ID']
        try:
            # PubChem PUG REST: search by name (ChEBI ID as synonym)
            url = f'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{chebi_id}/property/InChIKey,InChI,CanonicalSMILES/JSON'
            resp = requests.get(url, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                props = data.get('PropertyTable', {}).get('Properties', [])
                if props:
                    p = props[0]
                    cache[chebi_id] = {
                        'inchikey': p.get('InChIKey', ''),
                        'inchi': p.get('InChI', ''),
                        'smiles': p.get('CanonicalSMILES', ''),
                        'cid': str(p.get('CID', '')),
                    }
                else:
                    cache[chebi_id] = {'inchikey': '', 'inchi': '', 'smiles': '', 'cid': ''}
            else:
                print(f'  {chebi_id}: HTTP {resp.status_code}')
                cache[chebi_id] = {'inchikey': '', 'inchi': '', 'smiles': '', 'cid': ''}
        except Exception as ex:
            print(f'  {chebi_id}: {ex}')
            cache[chebi_id] = {'inchikey': '', 'inchi': '', 'smiles': '', 'cid': ''}

        if (idx + 1) % 50 == 0:
            found = sum(1 for v in cache.values() if v.get('inchikey'))
            print(f'  {idx+1}/{len(to_fetch)} ({found} with InChI Key)')
            with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(cache, f, ensure_ascii=False)

        time.sleep(BATCH_INTERVAL)

    # Final cache save
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False)
else:
    print('All cached, no fetch needed')

# ---- Step 4: stats ----
found = sum(1 for v in cache.values() if v.get('inchikey'))
missing = sum(1 for v in cache.values() if not v.get('inchikey'))
print(f'\nFound InChI Key: {found}, Missing: {missing}')

# ---- Step 5: write output ----
fields = ['ChEBI ID', 'Name', 'InChI Key', 'InChI', 'SMILES', 'PubChem CID']

with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fields, delimiter='\t', extrasaction='ignore')
    writer.writeheader()
    for comp in compounds:
        chebi_id = comp['ChEBI ID']
        info = cache.get(chebi_id, {})
        row = {
            'ChEBI ID': chebi_id,
            'Name': comp['Name'],
            'InChI Key': info.get('inchikey', ''),
            'InChI': info.get('inchi', ''),
            'SMILES': info.get('smiles', ''),
            'PubChem CID': info.get('cid', ''),
        }
        writer.writerow(row)

print(f'Written: {len(compounds)} rows, 6 cols -> {OUTPUT}')

# Sample
print('\nSample:')
print('-' * 80)
with open(OUTPUT, 'r', encoding='utf-8') as f:
    for i, row in enumerate(csv.DictReader(f, delimiter='\t')):
        if i >= 5: break
        cid = row['ChEBI ID']
        nm = row['Name']
        ik = row['InChI Key']
        sm = row.get('SMILES', 'N/A')
        print(f'{cid}: {nm}')
        print(f'  InChI Key: {ik}')
        print(f'  SMILES:    {sm[:80]}')

# Clean up
if os.path.exists(CACHE_FILE):
    os.remove(CACHE_FILE)
    print(f'\nCache removed.')
