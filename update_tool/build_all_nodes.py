"""
Generate all_nodes.tsv (图节点表).
Format: ChEBI ID, Name, InChI Key

构造逻辑:
  - 化合物集合 = terpene_only 所有 Substrate/Product ChEBI 去重, 去掉 GENERIC:*
  - Name        = chebi_full.tsv 的 Name
  - InChI Key   = PubChem (通过 ChEBI ID 查询). 若给定一个已有的 all_nodes 文件作第 4 参数,
                  则直接复用其中已抓好的 InChI Key (用于恢复/验证, 不联网); 否则走 PubChem 抓取
  - 排序        = ChEBI ID 字符串升序 (与旧表一致)
"""
import csv
import json
import os
import sys
import time

import requests

# 用法: python build_all_nodes.py [terpene_only] [chebi_full] [输出] [可选: 已有all_nodes复用它键]
TERPENE_INPUT = sys.argv[1] if len(sys.argv) > 1 else '../for_graph/uniprotkb_terpene_only.tsv'
CHEBI_INPUT = sys.argv[2] if len(sys.argv) > 2 else 'chebi_data/chebi_full.tsv'
OUTPUT = sys.argv[3] if len(sys.argv) > 3 else 'output_all_nodes.tsv'
REUSE = sys.argv[4] if len(sys.argv) > 4 else ''

# 缓存放脚本所在目录 (共享, 与输出目录无关): 自定义输出目录时也复用已抓好的键
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_allnodes_inchikey.json')
BATCH_INTERVAL = 0.25  # PubChem 限速

# ---- Step 1: 化合物集合 + 名称 ----
comps = {}
with open(TERPENE_INPUT, 'r', encoding='utf-8') as f:
    for r in csv.DictReader(f, delimiter='\t'):
        for c in (r['Substrate ChEBI'], r['Product ChEBI']):
            if c and not c.startswith('GENERIC'):
                comps.setdefault(c, '')
with open(CHEBI_INPUT, 'r', encoding='utf-8') as f:
    for r in csv.DictReader(f, delimiter='\t'):
        if r['ChEBI ID'] in comps:
            comps[r['ChEBI ID']] = r['Name']
chebi_ids = sorted(comps.keys())
print(f'Distinct non-GENERIC compounds: {len(chebi_ids)}')

# ---- Step 2: InChI Key ----
cache = {}
if REUSE and os.path.exists(REUSE):
    with open(REUSE, 'r', encoding='utf-8') as f:
        for r in csv.DictReader(f, delimiter='\t'):
            if r.get('InChI Key'):
                cache[r['ChEBI ID']] = r['InChI Key']
    print(f'Reused {len(cache)} InChI Keys from {REUSE}')
elif os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, 'r', encoding='utf-8') as f:
        cache = json.load(f)
    print(f'Loaded {len(cache)} keys from cache')

to_fetch = [c for c in chebi_ids if c not in cache]
if to_fetch:
    print(f'Fetching {len(to_fetch)} InChI Keys from PubChem...')
    for idx, cid in enumerate(to_fetch):
        try:
            url = (f'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/'
                   f'{cid}/property/InChIKey/JSON')
            resp = requests.get(url, timeout=30)
            if resp.status_code == 200:
                props = resp.json().get('PropertyTable', {}).get('Properties', [])
                key = (props[0].get('InChIKey', '') if props else '')
                if key:
                    cache[cid] = key
                else:
                    # 查到了但为空 (化合物可能无 InChI Key): 不缓存, 留待下次重试
                    print(f'  {cid}: empty InChIKey (not cached)')
            else:
                # HTTP 失败: 不缓存, 下次重试
                print(f'  {cid}: HTTP {resp.status_code} (not cached)')
        except Exception as e:
            # 网络异常: 不缓存, 下次重试
            print(f'  {cid}: {e} (not cached)')
        if (idx + 1) % 50 == 0:
            print(f'  {idx+1}/{len(to_fetch)}')
        time.sleep(BATCH_INTERVAL)
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False)

found = sum(1 for c in chebi_ids if cache.get(c))
print(f'InChI Key found: {found}/{len(chebi_ids)}')

# ---- Step 3: 输出 ----
fields = ['ChEBI ID', 'Name', 'InChI Key']
with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f, delimiter='\t')
    w.writerow(fields)
    for cid in chebi_ids:
        w.writerow([cid, comps[cid], cache.get(cid, '')])

print(f'Done! {len(chebi_ids)} rows -> {OUTPUT}')
