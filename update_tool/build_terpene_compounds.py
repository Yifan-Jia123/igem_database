"""
Generate uniprotkb_terpene_compounds.tsv (化合物卡片表的底表).
Format: ChEBI ID, Name, SMILES, Molecular Mass, ChEBI URL

构造逻辑:
  - 化合物集合 = terpene_only 所有 Substrate/Product ChEBI 去重, 去掉 GENERIC:*
  - 每行 = chebi_full.tsv 中对应 ChEBI ID 的整行 (5 列原样)
  - 排序 = ChEBI ID 字符串升序 (与旧表一致, 已验证 0 差异)
"""
import csv
import sys

# 用法: python build_terpene_compounds.py [terpene_only] [chebi_full] [输出]
TERPENE_INPUT = sys.argv[1] if len(sys.argv) > 1 else '../for_graph/uniprotkb_terpene_only.tsv'
CHEBI_INPUT = sys.argv[2] if len(sys.argv) > 2 else 'chebi_data/chebi_full.tsv'
OUTPUT = sys.argv[3] if len(sys.argv) > 3 else 'output_terpene_compounds.tsv'

# ---- 化合物集合 ----
comps = set()
with open(TERPENE_INPUT, 'r', encoding='utf-8') as f:
    for r in csv.DictReader(f, delimiter='\t'):
        for c in (r['Substrate ChEBI'], r['Product ChEBI']):
            if c and not c.startswith('GENERIC'):
                comps.add(c)
print(f'Distinct non-GENERIC compounds: {len(comps)}')

# ---- 过滤 chebi_full ----
rows = []
with open(CHEBI_INPUT, 'r', encoding='utf-8') as f:
    for r in csv.DictReader(f, delimiter='\t'):
        if r['ChEBI ID'] in comps:
            rows.append(r)
rows.sort(key=lambda r: r['ChEBI ID'])
print(f'Matched in chebi_full: {len(rows)}')

fields = ['ChEBI ID', 'Name', 'SMILES', 'Molecular Mass', 'ChEBI URL']
with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, delimiter='\t', fieldnames=fields)
    w.writeheader()
    w.writerows(rows)

print(f'Done! {len(rows)} rows -> {OUTPUT}')
